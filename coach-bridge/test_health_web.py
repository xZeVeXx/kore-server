"""Websuche im Gesundheits-Chat nur auf ausdruecklichen Wunsch (0.13 S13, Entscheidung 11.10.).

Vertrag: optionales JSON-Boolean `health_web_allowed` auf `POST /coach` (synchron und mit
`client_message_id`). Es wirkt nur im Health-Scope (`health_context_allowed: true`, Consent-Scope 2).
Ohne das Feld, mit false und auf `/coach/adjust-today` bleibt `web` im Health-Scope gesperrt.
"""
import http.client
import io
import json
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import bridge
from test_runner_stream_json import (
    CONVERSATION,
    FIXTURE_SESSION,
    TEST_TOKEN,
    arg_value,
    hermes_process,
    reply_stream,
    result_record,
)
from test_turns import FakeHermes, new_id, records, text, wait_until

CONTRACT = Path(__file__).resolve().parent.parent / "contract" / "app-required-capabilities.json"
HEALTH = {"health_context_allowed": True, "consent_scope_version": bridge.CONSENT_SCOPE_VERSION}
NOT_BOOLEANS = ("true", "false", "yes", 1, 0, None, [], {}, [True], 1.0)


def health_request(**extra) -> dict:
    return {"message": "Hallo", "conversation_id": CONVERSATION, **HEALTH, **extra}


def general_request(**extra) -> dict:
    return {"message": "Hallo", "conversation_id": CONVERSATION,
            "health_context_allowed": False, **extra}


def toolsets_of(args) -> list:
    return arg_value(args, "-t").split(",")


class HealthWebBase(unittest.TestCase):
    def setUp(self):
        for name, value in (
            ("ALLOW_ARGV_PROMPTS", False),
            ("PROMPT_TRANSPORT", "stdin"),
            ("INSTANCE_ID", "test-bridge"),
            ("PROFILE", "kore-coach"),
            ("PLAN_ORIGIN_SECRET", ""),
            ("PLAN_ORIGIN_PRINCIPAL", ""),
            ("TOKEN", TEST_TOKEN),
            ("TIMEOUT", 150),
            ("HERMES", "/opt/hermes/bin/hermes"),
        ):
            patcher = patch.object(bridge, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def with_origin_ticket(self):
        for name, value in (("PLAN_ORIGIN_SECRET", "s" * 48), ("PLAN_ORIGIN_PRINCIPAL", "kore")):
            patcher = patch.object(bridge, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.assertTrue(bridge.plan_origin_configured())


class ToolsetSelectionTests(HealthWebBase):
    def test_default_health_toolsets_never_carry_web(self):
        self.assertEqual(bridge.toolsets_for("health", True), ("skills", "memory", "kore"))
        self.assertEqual(bridge.toolsets_for("health", False), ("skills", "memory"))
        self.assertEqual(bridge.toolsets_for("health", True, health_web=False), ("skills", "memory", "kore"))
        self.assertEqual(bridge.toolsets_for("health", False, health_web=False), ("skills", "memory"))

    def test_the_explicit_flag_adds_web_in_front_with_and_without_ticket(self):
        self.assertEqual(bridge.toolsets_for("health", True, health_web=True),
                         ("web", "skills", "memory", "kore"))
        self.assertEqual(bridge.toolsets_for("health", False, health_web=True),
                         ("web", "skills", "memory"))
        self.assertEqual(bridge.HEALTH_WITH_WEB_TOOLSETS, ("web", "skills", "memory", "kore"))

    def test_only_the_boolean_true_opens_web_internal_callers_fail_closed(self):
        for value in ("true", "yes", 1, [True], object()):
            with self.subTest(value=value):
                self.assertEqual(bridge.toolsets_for("health", True, health_web=value),
                                 ("skills", "memory", "kore"))

    def test_the_request_helper_needs_both_fields_to_be_the_boolean_true(self):
        both = {"health_context_allowed": True, "health_web_allowed": True}
        self.assertTrue(bridge.health_web_requested(both))
        for change in ({"health_context_allowed": False}, {"health_context_allowed": "true"},
                       {"health_web_allowed": False}, {"health_web_allowed": 1},
                       {"health_web_allowed": "true"}):
            with self.subTest(change=change):
                self.assertFalse(bridge.health_web_requested({**both, **change}))
        self.assertFalse(bridge.health_web_requested({"health_context_allowed": True}))
        self.assertFalse(bridge.health_web_requested({"health_web_allowed": True}))

    def test_the_general_scope_ignores_the_flag(self):
        self.assertEqual(bridge.toolsets_for("general", True, health_web=True), ("web", "skills", "memory"))
        self.assertEqual(bridge.toolsets_for("general", False, health_web=True), ("web", "skills", "memory"))

    def test_the_default_allowlist_constant_stays_without_web_for_health(self):
        self.assertEqual(bridge.APP_CHAT_TOOLSETS["health"], ("skills", "memory", "kore"))
        self.assertEqual(bridge.HEALTH_SCOPE_EXCLUDED_TOOLSETS, ("web",))


class HermesArgsGuardTests(HealthWebBase):
    BASE = {"profile": "kore-coach", "scope": "health", "run_budget_s": 135, "max_turns": "6"}

    def test_web_in_the_health_scope_is_refused_without_the_explicit_parameter(self):
        for toolsets in (("web", "skills", "memory"), ("web", "skills", "memory", "kore"), ("web",)):
            for extra in ({}, {"allow_health_web": False}, {"allow_health_web": None},
                          {"allow_health_web": "yes"}, {"allow_health_web": 1}):
                with self.subTest(toolsets=toolsets, extra=extra), self.assertRaises(ValueError):
                    bridge.hermes_args(**self.BASE, toolsets=toolsets, **extra)

    def test_the_explicit_parameter_lets_web_through_in_the_health_scope_only(self):
        args = bridge.hermes_args(**self.BASE, toolsets=("web", "skills", "memory", "kore"),
                                  allow_health_web=True)
        self.assertEqual(arg_value(args, "--source"), "kore-health")
        self.assertEqual(arg_value(args, "-t"), "web,skills,memory,kore")

    def test_the_parameter_does_not_loosen_any_other_rule(self):
        for toolsets in (("web", "terminal"), ("web", "browser"), ("hermes-cli",), ("session_search",), ()):
            with self.subTest(toolsets=toolsets), self.assertRaises(ValueError):
                bridge.hermes_args(**self.BASE, toolsets=toolsets, allow_health_web=True)

    def test_the_default_call_without_the_parameter_is_unchanged(self):
        args = bridge.hermes_args(**self.BASE, toolsets=("skills", "memory", "kore"))
        self.assertEqual(arg_value(args, "-t"), "skills,memory,kore")


class SynchronousRequestTests(HealthWebBase):
    def post(self, path, payload):
        body = json.dumps(payload).encode("utf-8")
        handler = object.__new__(bridge.Handler)
        handler.path = path
        handler.headers = http.client.HTTPMessage()
        handler.headers["Authorization"] = f"Bearer {TEST_TOKEN}"
        handler.headers["Content-Length"] = str(len(body))
        handler.rfile = io.BytesIO(body)
        sent = []
        handler._send = lambda code, obj: sent.append((code, obj))
        handler.do_POST()
        return sent[0]

    @patch("bridge._run_process_bounded")
    def test_flag_true_in_the_health_scope_adds_web(self, run):
        run.side_effect = hermes_process(reply_stream())

        code, payload = self.post("/coach", health_request(health_web_allowed=True))

        self.assertEqual(code, 200, payload)
        args = run.call_args.args[0]
        self.assertEqual(arg_value(args, "--source"), "kore-health")
        self.assertEqual(toolsets_of(args), ["web", "skills", "memory"])

    @patch("bridge._run_process_bounded")
    def test_flag_true_with_an_origin_ticket_adds_web_next_to_kore(self, run):
        self.with_origin_ticket()
        run.side_effect = hermes_process(reply_stream())

        code, payload = self.post("/coach", health_request(health_web_allowed=True))

        self.assertEqual(code, 200, payload)
        self.assertEqual(toolsets_of(run.call_args.args[0]), ["web", "skills", "memory", "kore"])

    @patch("bridge._run_process_bounded")
    def test_flag_false_and_absent_keep_the_health_default_without_web(self, run):
        self.with_origin_ticket()
        for label, extra in (("absent", {}), ("false", {"health_web_allowed": False})):
            with self.subTest(label):
                run.reset_mock()
                run.side_effect = hermes_process(reply_stream())

                code, payload = self.post("/coach", health_request(**extra))

                self.assertEqual(code, 200, payload)
                self.assertEqual(toolsets_of(run.call_args.args[0]), ["skills", "memory", "kore"])

    @patch("bridge._run_process_bounded")
    def test_the_matrix_web_only_where_the_contract_says(self, run):
        # Allgemein: immer web. Health: web genau dann, wenn das Feld true ist.
        for ticket in (False, True):
            if ticket:
                self.with_origin_ticket()
            for scope_request, in_health in ((general_request, False), (health_request, True)):
                for flag in ("absent", False, True):
                    extra = {} if flag == "absent" else {"health_web_allowed": flag}
                    with self.subTest(ticket=ticket, health=in_health, flag=flag):
                        run.reset_mock()
                        run.side_effect = hermes_process(reply_stream())

                        code, payload = self.post("/coach", scope_request(**extra))

                        self.assertEqual(code, 200, payload)
                        has_web = "web" in toolsets_of(run.call_args.args[0])
                        self.assertEqual(has_web, (not in_health) or flag is True)

    @patch("bridge._run_process_bounded")
    def test_a_non_boolean_flag_is_400_before_any_run(self, run):
        for build in (health_request, general_request):
            for value in NOT_BOOLEANS:
                with self.subTest(health=build is health_request, value=value):
                    code, payload = self.post("/coach", build(health_web_allowed=value))

                    self.assertEqual(code, 400)
                    self.assertIn("health_web_allowed", payload["error"])
        run.assert_not_called()

    @patch("bridge._run_process_bounded")
    def test_the_general_scope_ignores_flag_true(self, run):
        run.side_effect = hermes_process(reply_stream())

        code, payload = self.post("/coach", general_request(health_web_allowed=True))

        self.assertEqual(code, 200, payload)
        args = run.call_args.args[0]
        self.assertEqual(arg_value(args, "--source"), "kore-general")
        self.assertEqual(toolsets_of(args), ["web", "skills", "memory"])
        self.assertEqual(payload["context"], "new")

    @patch("bridge._run_process_bounded")
    def test_flag_true_without_consent_scope_is_still_the_consent_error(self, run):
        # Das Feld ersetzt keine Freigabe: health_context_allowed:true braucht weiter Consent-Scope 2.
        code, _ = self.post("/coach", {
            "message": "Hallo", "conversation_id": CONVERSATION,
            "health_context_allowed": True, "health_web_allowed": True,
        })

        self.assertEqual(code, 403)
        run.assert_not_called()

    @patch("bridge._run_process_bounded")
    def test_switching_the_flag_between_turns_keeps_the_session(self, run):
        # Dieselbe Chat-ID, dieselbe Session: nur die -t-Liste wechselt, nie ein Reset.
        seen = []
        session = None
        for flag in ("absent", True, False, True):
            extra = {} if flag == "absent" else {"health_web_allowed": flag}
            if session is not None:
                extra["session"] = session
            run.side_effect = hermes_process(reply_stream(session_id=FIXTURE_SESSION))

            code, payload = self.post("/coach", health_request(**extra))

            self.assertEqual(code, 200, payload)
            args = run.call_args.args[0]
            seen.append(("web" in toolsets_of(args), payload["context"],
                         arg_value(args, "--resume") if "--resume" in args else None))
            self.assertNotIn("reset_reason", payload)
            session = payload["session"]
            self.assertEqual(bridge.verify_session(session, "health", CONVERSATION), FIXTURE_SESSION)

        self.assertEqual(seen, [
            (False, "new", None),
            (True, "continued", FIXTURE_SESSION),
            (False, "continued", FIXTURE_SESSION),
            (True, "continued", FIXTURE_SESSION),
        ])

    @patch("bridge._run_process_bounded")
    def test_the_prompt_does_not_change_with_the_flag(self, run):
        # Das Feld schaltet nur die Werkzeugliste; der Gesundheitskontext im Prompt bleibt gleich.
        prompts = []

        def capture(args, **kwargs):
            prompts.append(kwargs["input_bytes"])
            return hermes_process(reply_stream())(args, **kwargs)

        run.side_effect = capture
        context = {"readiness": 70}
        self.post("/coach", health_request(context=context))
        self.post("/coach", health_request(context=context, health_web_allowed=True))

        self.assertEqual(len(prompts), 2)
        self.assertEqual(prompts[0], prompts[1])


class AdjustTodayTests(HealthWebBase):
    SESSION = {"name": "Push", "exercises": [{"name": "Bank", "sets": [{"reps": 8, "weight_kg": 80}]}]}

    def post(self, payload):
        return SynchronousRequestTests.post(self, "/coach/adjust-today", payload)

    def request(self, **extra):
        return {"session": self.SESSION, **HEALTH, "context": {"readiness": 70}, **extra}

    @patch("bridge._run_process_bounded")
    def test_any_health_web_allowed_on_adjust_today_is_400_before_any_run(self, run):
        for value in (True, False, *NOT_BOOLEANS):
            with self.subTest(value=value):
                code, payload = self.post(self.request(health_web_allowed=value))

                self.assertEqual(code, 400)
                self.assertIn("health_web_allowed", payload["error"])
        run.assert_not_called()

    @patch("bridge._run_process_bounded")
    def test_adjust_today_without_the_field_never_gets_web(self, run):
        self.with_origin_ticket()
        answer = json.dumps({"session": self.SESSION, "rationale": "kurz", "changes": []})
        run.side_effect = hermes_process(reply_stream(answer))

        code, payload = self.post(self.request())

        self.assertEqual(code, 200, payload)
        self.assertEqual(toolsets_of(run.call_args.args[0]), ["skills", "memory"])

    @patch("bridge._run_process_bounded")
    def test_consent_errors_win_over_the_flag_error(self, run):
        # Ohne Health-Freigabe bleibt es der bekannte 403, nicht ein neuer 400.
        code, _ = self.post({"session": self.SESSION, "health_context_allowed": False,
                             "health_web_allowed": True})
        self.assertEqual(code, 403)
        run.assert_not_called()


class DetachedTurnTests(unittest.TestCase):
    """Dasselbe Feld auf dem Turn-Start (POST /coach mit client_message_id)."""

    def setUp(self):
        for name, value in (
            ("ALLOW_ARGV_PROMPTS", False),
            ("PROMPT_TRANSPORT", "stdin"),
            ("INSTANCE_ID", "test-bridge"),
            ("PROFILE", "kore-coach"),
            ("PLAN_ORIGIN_SECRET", ""),
            ("PLAN_ORIGIN_PRINCIPAL", ""),
            ("TOKEN", TEST_TOKEN),
            ("TIMEOUT", 150),
            ("HERMES", "/opt/hermes/bin/hermes"),
            ("KEEPALIVE_S", 15),
        ):
            patcher = patch.object(bridge, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.store = bridge.TurnStore()
        patcher = patch.object(bridge, "TURNS", self.store)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.server = bridge.ThreadingHTTPServer(("127.0.0.1", 0), bridge.Handler)
        threading.Thread(target=self.server.serve_forever, args=(0.01,), daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.port = self.server.server_address[1]

    def use_hermes(self, fake):
        for target, value in (
            ("bridge._run_process_bounded", fake),
            ("bridge._terminate_process_group", fake.terminate),
        ):
            patcher = patch(target, side_effect=value)
            self.addCleanup(patcher.stop)
            patcher.start()
        self.addCleanup(fake.release.set)
        return fake

    def start(self, build=health_request, **extra):
        body = json.dumps({**build(), "client_message_id": new_id(), **extra}).encode("utf-8")
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        connection.request("POST", "/coach", body=body, headers={
            "Content-Length": str(len(body)), "Authorization": f"Bearer {TEST_TOKEN}",
        })
        response = connection.getresponse()
        payload = json.loads(response.read())
        connection.close()
        return response.status, payload

    def run_started(self, build=health_request, **extra):
        fake = self.use_hermes(FakeHermes(block=False, before=records(text("OK"), result_record("OK"))))
        code, payload = self.start(build, **extra)
        self.assertEqual(code, 202, payload)
        wait_until(lambda: self.store.get(payload["turn_id"]).status != "running")
        self.assertEqual(len(fake.calls), 1)
        return fake.calls[0][0]

    def test_flag_true_reaches_the_worker_run(self):
        self.assertEqual(toolsets_of(self.run_started(health_web_allowed=True)),
                         ["web", "skills", "memory"])

    def test_flag_false_and_absent_stay_without_web_on_the_turn_path(self):
        self.assertEqual(toolsets_of(self.run_started(health_web_allowed=False)), ["skills", "memory"])
        self.setUp()
        self.assertEqual(toolsets_of(self.run_started()), ["skills", "memory"])

    def test_a_non_boolean_flag_is_400_and_creates_no_turn(self):
        fake = self.use_hermes(FakeHermes(block=False))
        for value in NOT_BOOLEANS:
            with self.subTest(value=value):
                code, payload = self.start(health_web_allowed=value)

                self.assertEqual(code, 400)
                self.assertIn("health_web_allowed", payload["error"])
        self.assertEqual(fake.calls, [])
        self.assertEqual(self.store.active_count(), 0)

    def test_the_general_scope_ignores_the_flag_on_the_turn_path(self):
        args = self.run_started(general_request, health_web_allowed=True)
        self.assertEqual(arg_value(args, "--source"), "kore-general")
        self.assertEqual(toolsets_of(args), ["web", "skills", "memory"])


class ContractSurfaceTests(HealthWebBase):
    def test_capability_is_listed_in_capabilities_and_health(self):
        self.assertIn("health_web_opt_in_v1", bridge.capabilities_payload()["capabilities"])
        self.assertIn("health_web_opt_in_v1", bridge.health_payload()["capabilities"])

    def test_capability_is_present_whatever_the_deployment_is_configured_with(self):
        with patch.object(bridge, "PLAN_ORIGIN_SECRET", ""), patch.object(bridge, "PROMPT_TRANSPORT", "argv"):
            self.assertIn("health_web_opt_in_v1", bridge.capabilities_payload()["capabilities"])

    def test_the_capability_is_optional_for_the_app(self):
        # Die App verlangt sie nicht; fehlt sie, blendet sie den Schalter aus.
        contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
        self.assertNotIn("health_web_opt_in_v1", contract)

    def test_health_reports_the_opt_in_list_next_to_the_defaults(self):
        toolsets = bridge.health_payload()["app_chat_toolsets"]
        self.assertEqual(toolsets, {
            "general": ["web", "skills", "memory"],
            "health": ["skills", "memory", "kore"],
            "health_with_web": ["web", "skills", "memory", "kore"],
        })

    def test_the_documented_request_field_is_named_exactly(self):
        self.assertEqual(bridge.HEALTH_WEB_FIELD, "health_web_allowed")


if __name__ == "__main__":
    unittest.main()
