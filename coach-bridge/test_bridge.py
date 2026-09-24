import base64
import hashlib
import hmac
import io
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import bridge
from test_runner_stream_json import (
    CONVERSATION,
    TEST_TOKEN,
    arg_value,
    hermes_process,
    reply_stream,
    stream_lines,
)


def sent_prompt(run):
    """Der uebergebene Prompt, unabhaengig vom Transport.

    Damit prueft jeder Inhaltstest denselben Text, egal ob er ueber stdin (Standard) oder
    ueber argv (Opt-in) gegangen ist — und ein Transportwechsel kann keinen Inhaltstest
    still in eine leere Behauptung verwandeln.
    """
    args = run.call_args.args[0]
    if "-q" in args:
        return args[args.index("-q") + 1]
    if args[args.index("--query-file") + 1] != "-":
        raise AssertionError("--query-file muss auf stdin zeigen, nie auf eine Datei")
    return run.call_args.kwargs["input_bytes"].decode("utf-8")


class BridgePrivacyTests(unittest.TestCase):
    def setUp(self):
        # Der Standard, nicht die Umgebung des Entwicklers: stdin-Transport, argv aus.
        self.argv_prompt_patcher = patch.object(bridge, "ALLOW_ARGV_PROMPTS", False)
        self.prompt_transport_patcher = patch.object(bridge, "PROMPT_TRANSPORT", "stdin")
        self.prompt_transport_patcher.start()
        self.addCleanup(self.prompt_transport_patcher.stop)
        self.instance_id_patcher = patch.object(bridge, "INSTANCE_ID", "test-bridge")
        self.profile_patcher = patch.object(bridge, "PROFILE", "test-profile")
        self.origin_secret_patcher = patch.object(bridge, "PLAN_ORIGIN_SECRET", "")
        self.origin_principal_patcher = patch.object(bridge, "PLAN_ORIGIN_PRINCIPAL", "")
        self.token_patcher = patch.object(bridge, "TOKEN", TEST_TOKEN)
        self.token_patcher.start()
        self.addCleanup(self.token_patcher.stop)
        self.argv_prompt_patcher.start()
        self.instance_id_patcher.start()
        self.profile_patcher.start()
        self.origin_secret_patcher.start()
        self.origin_principal_patcher.start()

    def tearDown(self):
        self.origin_principal_patcher.stop()
        self.origin_secret_patcher.stop()
        self.profile_patcher.stop()
        self.instance_id_patcher.stop()
        self.argv_prompt_patcher.stop()

    def momentum_context(self, now=None, **overrides):
        now = now or datetime.now(timezone.utc)
        payload = {
            "plan_training_streak_days": 4,
            "plan_today_state": "available",
            "plan_completed_today": False,
            "plan_remaining_minutes": 12,
            "daily_momentum_streak_days": 9,
            "daily_today_state": "available",
            "daily_completed_today": False,
            "action": "mobility",
            "factor": "activity",
            "protection_reasons": [],
            "source_state": "readable",
            "computed_at": (now - timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
            "valid_until": (now + timedelta(days=1)).isoformat().replace("+00:00", "Z"),
        }
        payload.update(overrides)
        return {"kore_momentum": payload}

    def test_capabilities_advertise_the_enforced_consent_boundary(self):
        payload = bridge.capabilities_payload()

        self.assertEqual(payload["service"], "kore-coach-bridge")
        self.assertGreaterEqual(payload["api_version"], 2)
        self.assertTrue(payload["health_context_fail_closed"])
        self.assertEqual(bridge.CONSENT_SCOPE_VERSION, 2)
        self.assertIn("consent_scope_v2", payload["capabilities"])
        self.assertNotIn("consent_scope_v1", payload["capabilities"])
        self.assertIn("scoped_sessions_v1", payload["capabilities"])
        # 0.10: kein --safe-mode mehr; die Grenze „kein kore-MCP ohne Ticket" traegt die -t-Liste.
        self.assertNotIn("safe_mode_without_health_v1", payload["capabilities"])
        self.assertIn("app_chat_toolsets_v1", payload["capabilities"])
        self.assertIn("conversation_sessions_v2", payload["capabilities"])
        self.assertIn("adjust_today_requires_health_v1", payload["capabilities"])
        self.assertIn("bounded_subprocess_v1", payload["capabilities"])
        self.assertIn("child_env_allowlist_v1", payload["capabilities"])
        self.assertIn("momentum_safety_v1", payload["capabilities"])
        self.assertIn("momentum_safety_v2", payload["capabilities"])
        self.assertEqual(payload["prompt_transport"], "stdin")
        self.assertIn("stdin_prompt_transport_v1", payload["capabilities"])
        self.assertEqual(bridge.health_payload()["capabilities"], payload["capabilities"])
        self.assertTrue(bridge.health_payload()["ok"])

    def test_capabilities_name_the_argv_opt_in_so_an_operator_can_read_the_exposure(self):
        # /capabilities muss unterscheidbar machen, ob auf DIESER Installation der
        # Gesundheitskontext in der Prozessliste steht — sonst ist die Warnung nicht pruefbar.
        with patch.object(bridge, "ALLOW_ARGV_PROMPTS", True), patch.object(
            bridge, "PROMPT_TRANSPORT", "argv"
        ):
            payload = bridge.capabilities_payload()
        self.assertEqual(payload["prompt_transport"], "argv-explicit-opt-in")
        self.assertNotIn("stdin_prompt_transport_v1", payload["capabilities"])

    def test_plan_origin_capability_is_conditional_on_a_valid_complete_configuration(self):
        self.assertNotIn("plan_origin_v1", bridge.capabilities_payload()["capabilities"])

        with patch.object(bridge, "PLAN_ORIGIN_SECRET", "s" * 32), patch.object(
            bridge, "PLAN_ORIGIN_PRINCIPAL", "marius"
        ):
            self.assertIn("plan_origin_v1", bridge.capabilities_payload()["capabilities"])

        with patch.object(bridge, "PLAN_ORIGIN_SECRET", "s" * 32):
            self.assertNotIn("plan_origin_v1", bridge.capabilities_payload()["capabilities"])
            self.assertIn("both", bridge.plan_origin_configuration_error())
        with patch.object(bridge, "PLAN_ORIGIN_SECRET", "short"), patch.object(
            bridge, "PLAN_ORIGIN_PRINCIPAL", "marius"
        ):
            self.assertNotIn("plan_origin_v1", bridge.capabilities_payload()["capabilities"])
            self.assertIn("32", bridge.plan_origin_configuration_error())

    def test_chat_requires_a_canonical_lowercase_rfc4122_conversation_id(self):
        valid = "123e4567-e89b-42d3-a456-426614174000"
        base = {"message": "Hallo", "health_context_allowed": False}

        self.assertIsNotNone(bridge.chat_contract_error(base))
        self.assertIsNone(bridge.chat_contract_error({**base, "conversation_id": valid}))
        for invalid in (
            valid.upper(),
            "123e4567e89b42d3a456426614174000",
            " 123e4567-e89b-42d3-a456-426614174000",
            "not-a-uuid",
            "123e4567-e89b-42d3-7456-426614174000",
            None,
            42,
        ):
            self.assertIsNotNone(bridge.chat_contract_error({
                **base,
                "conversation_id": invalid,
            }))

    @patch("bridge.secrets.token_hex", return_value="ab" * 16)
    @patch("bridge.time.time", return_value=1_700_000_000)
    def test_plan_origin_ticket_has_deterministic_canonical_payload_and_signature(
        self, _clock, _nonce
    ):
        conversation_id = "123e4567-e89b-42d3-a456-426614174000"
        secret = "s" * 32
        payload = {
            "conversation_id": conversation_id,
            "expires_at": 1_700_000_180,
            "issued_at": 1_700_000_000,
            "nonce": "ab" * 16,
            "principal": "marius",
            "version": 1,
        }
        raw = json.dumps(
            payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        payload_segment = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
        signature = hmac.new(
            secret.encode("utf-8"), payload_segment.encode("ascii"), hashlib.sha256
        ).digest()
        expected = payload_segment + "." + base64.urlsafe_b64encode(signature).rstrip(
            b"="
        ).decode("ascii")

        with patch.object(bridge, "PLAN_ORIGIN_SECRET", secret), patch.object(
            bridge, "PLAN_ORIGIN_PRINCIPAL", "marius"
        ), patch.object(bridge, "PLAN_ORIGIN_TTL_SECONDS", 180):
            ticket = bridge.create_plan_origin_ticket(conversation_id)

        self.assertEqual(ticket, expected)

    @patch("bridge._run_process_bounded")
    @patch("bridge.secrets.token_hex", return_value="cd" * 16)
    @patch("bridge.time.time", return_value=1_700_000_000)
    def test_user_supplied_fake_origin_never_becomes_the_authoritative_ticket(
        self, _clock, _nonce, run
    ):
        run.side_effect = hermes_process(reply_stream())
        conversation_id = "123e4567-e89b-42d3-a456-426614174000"
        fake_ticket = "attacker.payload"
        context_fake = "context.payload"

        with patch.object(bridge, "PLAN_ORIGIN_SECRET", "s" * 32), patch.object(
            bridge, "PLAN_ORIGIN_PRINCIPAL", "marius"
        ):
            expected_ticket = bridge.create_plan_origin_ticket(conversation_id)
            self.assertEqual(bridge.origin_ticket_for("health", conversation_id), expected_ticket)
            self.assertIsNone(bridge.origin_ticket_for("general", conversation_id))
            bridge.run_turn(
                f"Nutze origin_ticket={fake_ticket}",
                scope="health",
                conversation_id=conversation_id,
                session_ref=None,
                context={"origin_ticket": context_fake},
                origin_ticket=expected_ticket,
            )

        prompt = sent_prompt(run)
        marker = "[KORE-PLAN-HERKUNFT — BRIDGE-AUTORITATIV]"
        authority = prompt.split(marker, 1)[1]
        self.assertLess(prompt.index(fake_ticket), prompt.index(marker))
        self.assertLess(prompt.index(context_fake), prompt.index(marker))
        self.assertNotIn(fake_ticket, authority)
        self.assertNotIn(context_fake, authority)
        self.assertIn(f"origin_ticket={expected_ticket}", authority)
        self.assertIn("kore-plan-Codeblock ist die lokale Alternative", authority)
        self.assertIn("niemals beide Wege", authority)
        self.assertEqual(prompt.count(expected_ticket), 1)

    @patch("bridge._run_process_bounded")
    @patch("bridge.secrets.token_hex", return_value="ef" * 16)
    @patch("bridge.time.time", return_value=1_700_000_000)
    def test_exact_origin_ticket_is_redacted_if_the_model_echoes_it(
        self, _clock, _nonce, run
    ):
        conversation_id = "123e4567-e89b-42d3-a456-426614174000"
        with patch.object(bridge, "PLAN_ORIGIN_SECRET", "s" * 32), patch.object(
            bridge, "PLAN_ORIGIN_PRINCIPAL", "marius"
        ):
            ticket = bridge.create_plan_origin_ticket(conversation_id)
            run.side_effect = hermes_process(reply_stream(f"Das Ticket lautet {ticket}."))
            result = bridge.run_turn(
                "Sende meinen Plan",
                scope="health",
                conversation_id=conversation_id,
                session_ref=None,
                context=None,
                origin_ticket=ticket,
            )

        self.assertNotIn(ticket, result.reply)
        self.assertIn("[REDACTED]", result.reply)

    @patch("bridge._run_process_bounded")
    def test_plan_tooling_is_explicitly_unavailable_without_an_origin_configuration(self, run):
        run.side_effect = hermes_process(reply_stream())
        conversation_id = "123e4567-e89b-42d3-a456-426614174000"

        bridge.run_turn(
            "Erstelle einen Plan",
            scope="health",
            conversation_id=conversation_id,
            session_ref=None,
            context={"readiness": {"score": 87}},
            origin_ticket=bridge.origin_ticket_for("health", conversation_id),
        )

        args = run.call_args.args[0]
        prompt = sent_prompt(run)
        # Ohne Ticket kein kore-Toolset — und trotzdem kein --safe-mode mehr (0.10).
        self.assertNotIn("--safe-mode", args)
        self.assertNotIn("kore", arg_value(args, "-t").split(","))
        self.assertIn("readiness", prompt)
        self.assertIn("MCP-Planvorschlags-Tooling ist für diese Anfrage nicht freigegeben", prompt)
        self.assertIn("Rufe propose_training_plan nicht auf", prompt)
        self.assertIn("kore-plan-Codeblock", prompt)
        self.assertNotIn("origin_ticket=", prompt)

    def test_denied_context_is_not_added_to_prompt(self):
        prompt = bridge.build_prompt(
            "Hallo", {"readiness": {"score": 87}}, health_context_allowed=False
        )

        self.assertIn("[DATENSCHUTZ]", prompt)
        self.assertNotIn("readiness", prompt)
        self.assertNotIn("87", prompt)

    @patch("bridge._run_process_bounded")
    def test_denied_context_runs_without_mcp_servers(self, run):
        run.side_effect = hermes_process(reply_stream("Allgemeine Antwort", "fresh"))

        result = bridge.run_turn(
            "Hallo",
            scope="general",
            conversation_id=CONVERSATION,
            session_ref="health:legacy-health-session",
            context={"readiness": {"score": 87}},
            origin_ticket=None,
        )

        args = run.call_args.args[0]
        self.assertNotIn("--safe-mode", args)
        self.assertEqual(arg_value(args, "-t"), "web,skills,memory")
        self.assertNotIn("--resume", args)
        self.assertNotIn("readiness", " ".join(args))
        self.assertNotIn("readiness", sent_prompt(run))
        self.assertFalse(result.used_kore)
        self.assertEqual(bridge.verify_session(result.session, "general", CONVERSATION), "fresh")

    @patch("bridge._run_process_bounded")
    def test_general_session_resumes_only_in_general_scope(self, run):
        run.side_effect = hermes_process(reply_stream("Antwort", "safe-session"))
        ref = bridge.sign_session("general", CONVERSATION, "safe-session")

        result = bridge.run_turn(
            "Weiter", scope="general", conversation_id=CONVERSATION, session_ref=ref,
            context=None, origin_ticket=None,
        )

        args = run.call_args.args[0]
        self.assertEqual(args[args.index("--resume") + 1], "safe-session")
        self.assertEqual(result.session, ref)
        self.assertEqual(result.context, "continued")

    @patch("bridge._run_process_bounded")
    def test_session_scope_change_does_not_resume(self, run):
        run.side_effect = hermes_process(reply_stream("Neue Antwort"))

        result = bridge.run_turn(
            "Hallo",
            scope="general",
            conversation_id=CONVERSATION,
            session_ref=bridge.sign_session("health", CONVERSATION, "private-session"),
            context=None,
            origin_ticket=None,
        )

        self.assertNotIn("--resume", run.call_args.args[0])
        self.assertEqual((result.context, result.reset_reason), ("reset", "invalid"))

    @patch("bridge._run_process_bounded")
    def test_origin_configured_authorized_context_keeps_max_profile_and_mcp(self, run):
        run.side_effect = hermes_process(reply_stream("Antwort", "health-session"))

        with patch.object(bridge, "PLAN_ORIGIN_SECRET", "s" * 32), patch.object(
            bridge, "PLAN_ORIGIN_PRINCIPAL", "marius"
        ):
            result = bridge.run_turn(
                "Hallo",
                scope="health",
                conversation_id=CONVERSATION,
                session_ref=bridge.sign_session("health", CONVERSATION, "legacy-session"),
                context={"readiness": {"score": 87}},
                origin_ticket=bridge.origin_ticket_for("health", CONVERSATION),
            )

        args = run.call_args.args[0]
        self.assertNotIn("--safe-mode", args)
        self.assertEqual(arg_value(args, "-p"), "test-profile")
        self.assertEqual(arg_value(args, "-t"), "web,skills,memory,kore")
        self.assertEqual(args[args.index("--resume") + 1], "legacy-session")
        self.assertIn("readiness", sent_prompt(run))
        self.assertTrue(result.used_kore)
        self.assertEqual(
            bridge.verify_session(result.session, "health", CONVERSATION), "health-session"
        )

    @patch("bridge._run_process_bounded")
    def test_used_kore_recognizes_hermes_mcp_tool_names(self, run):
        self.assertIn("mcp__kore__", bridge.USED_KORE_MARKERS)
        run.side_effect = hermes_process(stream_lines(
            {"type": "tool_use", "name": "mcp__kore__get_recovery_today", "input": {}},
            {"type": "text", "text": "Deine Erholung liegt heute im Band."},
            {"type": "result", "session_id": "health-session", "exit_code": 0,
             "text": "Deine Erholung liegt heute im Band."},
        ))

        result = bridge.run_turn(
            "Wie steht es?", scope="health", conversation_id=CONVERSATION, session_ref=None,
            context=None, origin_ticket="tkt.sig",
        )

        self.assertTrue(result.used_kore)

        # Der Werkzeugname nur auf stderr zaehlt nicht mehr: stderr ist nie Quelle.
        run.side_effect = hermes_process(
            reply_stream("Antwort"), stderr="┊ mcp__kore__get_recovery_today 0.2s"
        )
        result = bridge.run_turn(
            "Wie steht es?", scope="health", conversation_id=CONVERSATION, session_ref=None,
            context=None, origin_ticket="tkt.sig",
        )
        self.assertFalse(result.used_kore)

    def test_adjustment_fails_closed_without_explicit_consent(self):
        code, payload = bridge.adjust_today({
            "session": {"name": "Push"},
            "health_context_allowed": False,
        })

        self.assertEqual(code, 403)
        self.assertIn("nicht freigegeben", payload["error"])

    def test_adjustment_instruction_matches_the_client_non_escalation_corridor(self):
        self.assertIn("Last 70–100 %", bridge.ADJUST_INSTRUCTION)
        self.assertNotIn("105 %", bridge.ADJUST_INSTRUCTION)

    def test_health_context_requires_current_scope_version(self):
        error = bridge.consent_contract_error({
            "health_context_allowed": True,
            "context": {"readiness": 87},
        })

        self.assertEqual(error[0], 403)
        self.assertIn("Consent-Scope", error[1]["error"])
        legacy = bridge.consent_contract_error({
            "health_context_allowed": True,
            "consent_scope_version": 1,
            "context": {"readiness": 87},
        })
        self.assertEqual(legacy[0], 403)
        self.assertIsNone(bridge.consent_contract_error({
            "health_context_allowed": True,
            "consent_scope_version": bridge.CONSENT_SCOPE_VERSION,
            "context": {"readiness": 87},
        }))

    def test_general_chat_rejects_smuggled_context_and_accepts_explicit_safe_mode(self):
        error = bridge.consent_contract_error({
            "health_context_allowed": False,
            "context": {"readiness": 87},
        })

        self.assertEqual(error[0], 400)
        self.assertIsNone(bridge.consent_contract_error({"health_context_allowed": False}))

    def test_momentum_contract_accepts_only_the_exact_typed_projection(self):
        context = self.momentum_context()
        self.assertIsNone(bridge.momentum_context_error(context))

        extra = self.momentum_context()
        extra["kore_momentum"]["weekly_goal"] = 120
        self.assertEqual(bridge.momentum_context_error(extra), "fields")

        boolean_count = self.momentum_context(plan_training_streak_days=True)
        self.assertEqual(
            bridge.momentum_context_error(boolean_count), "plan_training_streak_days"
        )
        unknown_state = self.momentum_context(plan_today_state="lost")
        self.assertEqual(bridge.momentum_context_error(unknown_state), "plan_today_state")
        unsorted_reasons = self.momentum_context(
            protection_reasons=["lowRecovery", "highStrain"]
        )
        self.assertEqual(
            bridge.momentum_context_error(unsorted_reasons), "protection_reasons"
        )
        naive_time = self.momentum_context(computed_at="2026-07-11T12:00:00")
        self.assertEqual(bridge.momentum_context_error(naive_time), "computed_at")

    def test_momentum_contract_accepts_only_canonical_optional_sleep_timing(self):
        now = datetime(2026, 7, 11, 12, tzinfo=timezone.utc)
        sleep_base = {
            "action": "sleep",
            "factor": "sleep",
            "protection_reasons": ["sleepPriority"],
        }
        for phase in sorted(bridge._MOMENTUM_SLEEP_PREPARATION_PHASES):
            with self.subTest(valid_phase=phase):
                self.assertIsNone(bridge.momentum_context_error(self.momentum_context(
                    now,
                    **sleep_base,
                    sleep_preparation_phase=phase,
                )))
        self.assertIsNone(bridge.momentum_context_error(self.momentum_context(
            now,
            **sleep_base,
            sleep_preparation_phase="laterToday",
            sleep_preparation_opens_at=(now + timedelta(hours=8)).isoformat().replace(
                "+00:00", "Z"
            ),
        )))

        invalid_cases = [
            (
                "unknown phase",
                {**sleep_base, "sleep_preparation_phase": "tonightMaybe"},
                "sleep_preparation_phase",
            ),
            (
                "opening without phase",
                {
                    **sleep_base,
                    "sleep_preparation_opens_at": now.isoformat().replace("+00:00", "Z"),
                },
                "sleep_preparation_opens_at",
            ),
            (
                "naive opening",
                {
                    **sleep_base,
                    "sleep_preparation_phase": "laterToday",
                    "sleep_preparation_opens_at": "2026-07-11T20:00:00",
                },
                "sleep_preparation_opens_at",
            ),
            (
                "non-sleep action",
                {
                    **sleep_base,
                    "action": "recovery",
                    "sleep_preparation_phase": "laterToday",
                },
                "sleep_preparation",
            ),
            (
                "non-sleep factor",
                {
                    **sleep_base,
                    "factor": "recovery",
                    "sleep_preparation_phase": "laterToday",
                },
                "sleep_preparation",
            ),
            (
                "missing sleep protection",
                {
                    **sleep_base,
                    "protection_reasons": [],
                    "sleep_preparation_phase": "laterToday",
                },
                "sleep_preparation",
            ),
        ]
        for label, overrides, expected in invalid_cases:
            with self.subTest(invalid=label):
                self.assertEqual(
                    bridge.momentum_context_error(self.momentum_context(now, **overrides)),
                    expected,
                )

    def test_momentum_relational_contradictions_are_unclear(self):
        now = datetime(2026, 7, 11, 12, tzinfo=timezone.utc)
        cases = [
            ("plan completed but available", {"plan_completed_today": True}),
            ("plan secured but incomplete", {"plan_today_state": "secured"}),
            ("daily completed but available", {"daily_completed_today": True}),
            ("daily secured but incomplete", {"daily_today_state": "secured"}),
            (
                "plan completion without daily completion",
                {"plan_today_state": "secured", "plan_completed_today": True},
            ),
            (
                "computed at expiry",
                {
                    "computed_at": (now + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
                    "valid_until": (now + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
                },
            ),
        ]
        for label, overrides in cases:
            with self.subTest(case=label):
                self.assertEqual(
                    bridge.momentum_safety_state(self.momentum_context(now, **overrides), now=now),
                    "unclear",
                )

        canonical_secured = self.momentum_context(
            now,
            plan_today_state="secured",
            plan_completed_today=True,
            daily_today_state="secured",
            daily_completed_today=True,
        )
        self.assertEqual(
            bridge.momentum_safety_state(canonical_secured, now=now), "no_pressure"
        )

    def test_momentum_protection_and_rest_states_disable_loss_pressure(self):
        now = datetime(2026, 7, 11, 12, tzinfo=timezone.utc)
        self.assertIn("noPlannedSession", bridge._MOMENTUM_PROTECTIONS)
        self.assertIn("timeZoneTransition", bridge._MOMENTUM_PROTECTIONS)
        cases = [
            self.momentum_context(now, plan_today_state="recoveryProtected"),
            self.momentum_context(now, plan_today_state="restDay"),
            self.momentum_context(now, daily_today_state="recoveryProtected"),
        ]
        for reason in sorted(bridge._MOMENTUM_PROTECTIONS):
            cases.append(self.momentum_context(now, protection_reasons=[reason]))

        for context in cases:
            self.assertEqual(bridge.momentum_safety_state(context, now=now), "protected")
            directive = bridge.momentum_safety_directive(context, now=now)
            self.assertIn("keinen Verlustdruck", directive)
            self.assertIn("priorisiere Recovery", directive)

    def test_unreadable_failed_future_and_expired_momentum_are_unclear(self):
        now = datetime(2026, 7, 11, 12, tzinfo=timezone.utc)
        cases = [
            self.momentum_context(now, source_state="unreadable"),
            self.momentum_context(now, source_state="failed"),
            self.momentum_context(now, plan_today_state="unreadable"),
            self.momentum_context(now, daily_today_state="unreadable"),
            self.momentum_context(
                now,
                computed_at=(now + timedelta(seconds=1)).isoformat().replace("+00:00", "Z"),
            ),
            self.momentum_context(
                now,
                valid_until=now.isoformat().replace("+00:00", "Z"),
            ),
        ]

        for context in cases:
            self.assertEqual(bridge.momentum_safety_state(context, now=now), "unclear")
            directive = bridge.momentum_safety_directive(context, now=now)
            self.assertIn("keinen Verfall", directive)
            self.assertIn("keine Gefährdung", directive)

        exactly_twelve_hours_old = self.momentum_context(
            now,
            computed_at=(now - timedelta(hours=12)).isoformat().replace("+00:00", "Z"),
            valid_until=(now + timedelta(hours=14)).isoformat().replace("+00:00", "Z"),
        )
        self.assertEqual(
            bridge.momentum_safety_state(exactly_twelve_hours_old, now=now), "no_pressure"
        )

        exact_maximum_span = self.momentum_context(
            now,
            computed_at=now.isoformat().replace("+00:00", "Z"),
            valid_until=(now + timedelta(hours=26)).isoformat().replace("+00:00", "Z"),
        )
        self.assertEqual(bridge.momentum_safety_state(exact_maximum_span, now=now), "no_pressure")
        overlong_span = self.momentum_context(
            now,
            computed_at=now.isoformat().replace("+00:00", "Z"),
            valid_until=(now + timedelta(hours=26, seconds=1)).isoformat().replace(
                "+00:00", "Z"
            ),
        )
        self.assertEqual(bridge.momentum_safety_state(overlong_span, now=now), "unclear")

    def test_momentum_pressure_is_track_specific_and_requires_feasible_training(self):
        now = datetime(2026, 7, 11, 12, tzinfo=timezone.utc)

        def pressure_context(**overrides):
            values = {
                "action": "training",
                "factor": "strength",
                "plan_today_state": "atRisk",
                "plan_remaining_minutes": 12,
                "valid_until": (now + timedelta(minutes=13)).isoformat().replace(
                    "+00:00", "Z"
                ),
            }
            values.update(overrides)
            return self.momentum_context(now, **values)

        cases = [
            ("plan", {}, "pressure_plan"),
            (
                "daily fallback",
                {"plan_today_state": "available", "daily_today_state": "atRisk"},
                "pressure_daily",
            ),
            (
                "daily fallback preserves raw plan risk truth",
                {
                    "plan_training_streak_days": 0,
                    "daily_today_state": "atRisk",
                },
                "pressure_daily",
            ),
            ("plan wins when both qualify", {"daily_today_state": "atRisk"}, "pressure_plan"),
            ("non-training action", {"action": "mobility", "factor": "activity"}, "no_pressure"),
            ("non-training factor", {"factor": "activity"}, "no_pressure"),
            ("zero plan streak", {"plan_training_streak_days": 0}, "no_pressure"),
            ("zero remaining minutes", {"plan_remaining_minutes": 0}, "no_pressure"),
            ("no at-risk track", {"plan_today_state": "available"}, "no_pressure"),
            (
                "daily streak is zero",
                {
                    "plan_today_state": "available",
                    "daily_today_state": "atRisk",
                    "daily_momentum_streak_days": 0,
                },
                "no_pressure",
            ),
            (
                "exact completion boundary",
                {
                    "valid_until": (now + timedelta(minutes=12)).isoformat().replace(
                        "+00:00", "Z"
                    ),
                },
                "no_pressure",
            ),
            (
                "one second before completion boundary",
                {
                    "valid_until": (now + timedelta(minutes=12, seconds=1)).isoformat().replace(
                        "+00:00", "Z"
                    ),
                },
                "pressure_plan",
            ),
        ]
        for label, overrides, expected in cases:
            with self.subTest(case=label):
                self.assertEqual(
                    bridge.momentum_safety_state(pressure_context(**overrides), now=now),
                    expected,
                )

        plan_directive = bridge.momentum_safety_directive(pressure_context(), now=now)
        self.assertIn("Ausschließlich für den Plan-Trainingsstreak", plan_directive)
        self.assertIn("Daily-Momentum-Spur nicht als gefährdet", plan_directive)
        both_at_risk_directive = bridge.momentum_safety_directive(
            pressure_context(daily_today_state="atRisk"), now=now
        )
        self.assertIn("priorisiere trotzdem nur den Plan", both_at_risk_directive)
        self.assertIn("Behaupte nicht, Daily sei gesichert oder ungefährdet", both_at_risk_directive)
        self.assertNotIn("Daily-Momentum-Spur nicht als gefährdet", both_at_risk_directive)
        daily_directive = bridge.momentum_safety_directive(pressure_context(
            plan_today_state="available", daily_today_state="atRisk"
        ), now=now)
        self.assertIn("Ausschließlich für den Daily-Momentum-Streak", daily_directive)
        self.assertIn("Plan-Trainingsstreak nicht als gefährdet", daily_directive)
        daily_fallback_with_raw_plan_risk = bridge.momentum_safety_directive(
            pressure_context(
                plan_training_streak_days=0,
                daily_today_state="atRisk",
            ),
            now=now,
        )
        self.assertIn("richte keinen zweiten Verlustappell", daily_fallback_with_raw_plan_risk)
        self.assertIn(
            "behaupte nicht, er sei gesichert oder ungefährdet",
            daily_fallback_with_raw_plan_risk,
        )
        neutral_directive = bridge.momentum_safety_directive(pressure_context(
            plan_today_state="available"
        ), now=now)
        self.assertIn("keinen Verlustdruck", neutral_directive)
        self.assertIn("keine Trainingsaufforderung zum Erhalt eines Streaks", neutral_directive)

    def test_momentum_prompt_appends_server_safety_after_context(self):
        protected = self.momentum_context(protection_reasons=["illness"])
        prompt = bridge.build_prompt("Heute trainieren?", protected, health_context_allowed=True)

        self.assertIn('"kore_momentum"', prompt)
        self.assertGreater(
            prompt.index("[KORE-MOMENTUM-SICHERHEITSVERTRAG]"),
            prompt.index("```json"),
        )
        self.assertIn("Diese Serverregel hat Vorrang", prompt)
        self.assertIn("keinen Verlustdruck", prompt)

        now = datetime.now(timezone.utc)
        at_risk = self.momentum_context(
            now,
            action="training",
            factor="cardio",
            plan_today_state="atRisk",
            valid_until=(now + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
        )
        pressure = bridge.momentum_safety_directive(at_risk, now=now)
        self.assertIn("Ausschließlich für den Plan-Trainingsstreak", pressure)
        self.assertIn("Daily-Momentum-Spur nicht als gefährdet", pressure)

    def test_consent_contract_rejects_malformed_momentum_before_hermes(self):
        malformed = self.momentum_context(plan_remaining_minutes=-1)
        error = bridge.consent_contract_error({
            "health_context_allowed": True,
            "consent_scope_version": bridge.CONSENT_SCOPE_VERSION,
            "context": malformed,
        })

        self.assertEqual(error[0], 400)
        self.assertIn("kore_momentum", error[1]["error"])

    def test_missing_bridge_token_is_never_an_authentication_bypass(self):
        with patch.object(bridge, "TOKEN", ""):
            self.assertEqual(bridge.authorization_status(None), 503)
            self.assertEqual(bridge.authorization_status("Bearer anything"), 503)
        with patch.object(bridge, "TOKEN", "secret"):
            self.assertEqual(bridge.authorization_status(None), 401)
            self.assertEqual(bridge.authorization_status("Bearer wrong"), 401)
            self.assertEqual(bridge.authorization_status("Bearer secret"), 200)

    def test_hermes_child_environment_is_allowlisted_and_drops_service_secrets(self):
        parent = {
            "KORE_BRIDGE_TOKEN": "bridge-secret",
            "KORE_PLAN_ORIGIN_SECRET": "origin-secret-must-not-cross",
            "KORE_SYNC_TOKEN": "sync-secret",
            "OPENROUTER_API_KEY": "provider-secret-in-wrong-place",
            "BWS_ACCESS_TOKEN": "secret-source-token",
            "PATH": "/untrusted/path",
            "LANG": "de_DE.UTF-8",
            "SSL_CERT_FILE": "/etc/ssl/custom.pem",
        }

        with patch.object(bridge, "HOME", "/home/hermes"), patch.object(
            bridge, "CHILD_PATH", "/usr/bin:/bin"
        ):
            child = bridge.hermes_child_env(parent)

        self.assertEqual(child, {
            "HOME": "/home/hermes",
            "PATH": "/usr/bin:/bin",
            "LANG": "de_DE.UTF-8",
            "SSL_CERT_FILE": "/etc/ssl/custom.pem",
        })
        self.assertFalse(any("TOKEN" in key or "KEY" in key for key in child))
        self.assertNotIn("KORE_PLAN_ORIGIN_SECRET", child)

    @patch("bridge._run_process_bounded")
    def test_run_turn_passes_only_the_allowlisted_child_environment(self, run):
        run.side_effect = hermes_process(reply_stream())

        with patch.dict(os.environ, {
            "KORE_BRIDGE_TOKEN": "bridge-secret",
            "UNRELATED_SECRET": "must-not-cross",
            "LANG": "C.UTF-8",
        }, clear=False):
            bridge.run_turn(
                "Hallo", scope="general", conversation_id=CONVERSATION, session_ref=None,
                context=None, origin_ticket=None,
            )

        child = run.call_args.kwargs["env"]
        self.assertEqual(child["HOME"], bridge.HOME)
        self.assertEqual(child["LANG"], "C.UTF-8")
        self.assertNotIn("KORE_BRIDGE_TOKEN", child)
        self.assertNotIn("UNRELATED_SECRET", child)

    @patch("bridge._run_process_bounded")
    def test_the_default_transport_keeps_the_prompt_out_of_the_process_list(self, run):
        run.side_effect = hermes_process(reply_stream())

        bridge.run_turn(
            "sensibler Gesundheitsprompt",
            scope="general",
            conversation_id=CONVERSATION,
            session_ref=None,
            context=None,
            origin_ticket=None,
        )

        args = run.call_args.args[0]
        # Der Kern der Sache: kein einziges argv-Element traegt den Prompt, und der Aufruf
        # zeigt --query-file auf stdin. /proc/<pid>/cmdline zeigt nichts Privates.
        self.assertNotIn("-q", args)
        self.assertEqual(args[args.index("--query-file") + 1], "-")
        self.assertNotIn("sensibler Gesundheitsprompt", " ".join(args))
        self.assertIn("sensibler Gesundheitsprompt", sent_prompt(run))
        self.assertIsInstance(run.call_args.kwargs["input_bytes"], bytes)

    @patch("bridge._run_process_bounded")
    def test_the_health_context_never_reaches_argv_on_the_default_transport(self, run):
        run.side_effect = hermes_process(reply_stream())

        bridge.run_turn(
            "Wie sieht es aus?",
            scope="health",
            conversation_id=CONVERSATION,
            session_ref=bridge.sign_session("health", CONVERSATION, "legacy-session"),
            context={"readiness": {"score": 87}},
            origin_ticket=None,
        )

        self.assertNotIn("readiness", " ".join(run.call_args.args[0]))
        self.assertIn("readiness", sent_prompt(run))

    @patch("bridge._run_process_bounded")
    def test_the_argv_opt_in_no_longer_starts_hermes(self, run):
        # 0.10: stream-json braucht Hermes >= v0.21.4, das --query-file kennt. Der argv-Weg
        # wuerde den Prompt in die Prozessliste legen und wird deshalb abgelehnt statt benutzt.
        with patch.object(bridge, "ALLOW_ARGV_PROMPTS", True), patch.object(
            bridge, "PROMPT_TRANSPORT", "argv"
        ):
            with self.assertRaises(bridge.PromptTransportUnavailable):
                bridge.run_turn(
                    "Hallo", scope="general", conversation_id=CONVERSATION, session_ref=None,
                    context=None, origin_ticket=None,
                )

        run.assert_not_called()

    @patch("bridge._run_process_bounded")
    def test_an_unknown_transport_fails_closed_instead_of_guessing(self, run):
        with patch.object(bridge, "PROMPT_TRANSPORT", "carrier-pigeon"):
            with self.assertRaises(bridge.PromptTransportUnavailable):
                bridge.run_turn(
                    "sensibler Prompt", scope="general", conversation_id=CONVERSATION,
                    session_ref=None, context=None, origin_ticket=None,
                )

        run.assert_not_called()

    def test_the_argv_opt_in_warns_that_chats_are_refused_and_the_default_does_not(self):
        with patch.object(bridge, "ALLOW_ARGV_PROMPTS", True):
            warning = bridge.runtime_configuration_warning()
        self.assertIsNotNone(warning)
        self.assertIn("KORE_HERMES_ALLOW_ARGV_PROMPTS", warning)
        self.assertIn("Prozessliste", warning)
        self.assertIn("lehnt", warning)
        with patch.object(bridge, "ALLOW_ARGV_PROMPTS", False):
            self.assertIsNone(bridge.runtime_configuration_warning())

    @patch("bridge._run_process_bounded")
    def test_exact_built_prompt_is_byte_bounded_before_popen(self, run):
        run.side_effect = hermes_process(reply_stream())
        turn = {
            "scope": "general", "conversation_id": CONVERSATION, "session_ref": None,
            "context": None, "origin_ticket": None,
        }

        with patch.object(bridge, "prompt_payload_limit", return_value=64), patch.object(
            bridge, "build_prompt", return_value="ä" * 32
        ):
            bridge.run_turn("Hallo", **turn)
        run.assert_called_once()

        run.reset_mock()
        with patch.object(bridge, "prompt_payload_limit", return_value=64), patch.object(
            bridge, "build_prompt", return_value="ä" * 32 + "x"
        ):
            with self.assertRaises(bridge.HermesPromptTooLarge) as raised:
                bridge.run_turn("Hallo", **turn)
        self.assertIn("Byte-Limit", str(raised.exception))
        run.assert_not_called()

    @patch("bridge.run_turn", side_effect=bridge.HermesPromptTooLarge("oversized"))
    def test_http_boundary_maps_prompt_limit_to_clear_413(self, _run):
        body = json.dumps({
            "message": "Hallo",
            "conversation_id": "123e4567-e89b-42d3-a456-426614174000",
            "health_context_allowed": False,
        }).encode("utf-8")
        handler = object.__new__(bridge.Handler)
        handler.path = "/coach"
        handler.headers = {
            "Authorization": "Bearer secret",
            "Content-Length": str(len(body)),
        }
        handler.rfile = io.BytesIO(body)
        sent = []
        handler._send = lambda code, payload: sent.append((code, payload))

        with patch.object(bridge, "TOKEN", "secret"):
            handler.do_POST()

        self.assertEqual(sent[0][0], 413)
        self.assertIn("zu großen Hermes-Prompt", sent[0][1]["error"])

    def test_maximum_ascii_app_payloads_fit_the_default_prompt_argument_budget(self):
        exercise = {
            "exercise_id": "123e4567-e89b-42d3-a456-426614174000",
            "name": "n" * 128,
            "note": "n" * 512,
            "sets": [
                {"weight_kg": 1_000.0, "reps": 49, "reps_max": 50}
                for _ in range(20)
            ],
        }
        session = {
            "session_id": "123e4567-e89b-42d3-a456-426614174000",
            "name": "s" * 128,
            "exercises": [
                {
                    **exercise,
                    "exercise_id": f"123e4567-e89b-42d3-a456-{index:012d}",
                    "name": f"{index:02d}" + "n" * 126,
                }
                for index in range(32)
            ],
        }
        self.assertTrue(bridge.validate_session(session))

        context = {"padding": "x" * (64 * 1_024 - 32)}
        self.assertLessEqual(
            len(json.dumps(context, separators=(",", ":")).encode("utf-8")),
            64 * 1_024,
        )
        adjust_message = bridge.ADJUST_INSTRUCTION + "\n\nSESSION:\n" + json.dumps(
            session, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        )
        adjust_prompt = bridge.build_prompt(
            adjust_message, context, health_context_allowed=True
        )
        chat_prompt = bridge.build_prompt(
            "m" * (16 * 1_024), context, health_context_allowed=True
        )
        self.assertLessEqual(len(adjust_prompt.encode("utf-8")), bridge.prompt_argument_limit())
        self.assertLessEqual(len(chat_prompt.encode("utf-8")), bridge.prompt_argument_limit())

    @patch("bridge._run_process_bounded")
    def test_escape_heavy_valid_adjustment_is_rejected_before_process_start(self, run):
        exercise = {
            "exercise_id": "123e4567-e89b-42d3-a456-426614174000",
            "name": "00" + "\x01" * 126,
            "note": "\x01" * 512,
            "sets": [{"weight_kg": 80.0, "reps": 8}],
        }
        session = {
            "session_id": "123e4567-e89b-42d3-a456-426614174000",
            "name": "s" * 128,
            "exercises": [
                {
                    **exercise,
                    "exercise_id": f"123e4567-e89b-42d3-a456-{index:012d}",
                    "name": f"{index:02d}" + "\x01" * 126,
                }
                for index in range(32)
            ],
        }
        self.assertTrue(bridge.validate_session(session))

        context = {"padding": "x" * (64 * 1_024 - 32)}
        request = {
            "session": session,
            "health_context_allowed": True,
            "consent_scope_version": bridge.CONSENT_SCOPE_VERSION,
            "context": context,
        }
        self.assertLessEqual(
            len(json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode("utf-8")),
            bridge.MAX_REQUEST_BYTES,
        )
        adjust_message = bridge.ADJUST_INSTRUCTION + "\n\nSESSION:\n" + json.dumps(
            session, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        )
        escaped_prompt = bridge.build_prompt(
            adjust_message, context, health_context_allowed=True
        )
        self.assertGreater(len(escaped_prompt.encode("utf-8")), bridge.prompt_argument_limit())

        with self.assertRaises(bridge.HermesPromptTooLarge):
            bridge.run_turn(
                adjust_message,
                scope="health",
                conversation_id=None,
                session_ref=None,
                context=context,
                origin_ticket=None,
                max_turns="4",
            )

        run.assert_not_called()

    def test_runtime_configuration_requires_explicit_instance_profile_and_token(self):
        with patch.object(bridge, "TOKEN", ""):
            self.assertIn("KORE_BRIDGE_TOKEN", bridge.runtime_configuration_error())
        with patch.object(bridge, "TOKEN", "configured"), patch.object(
            bridge, "INSTANCE_ID", ""
        ):
            self.assertIn("KORE_INSTANCE_ID", bridge.runtime_configuration_error())
        with patch.object(bridge, "TOKEN", "configured"), patch.object(
            bridge, "PROFILE", ""
        ):
            self.assertIn("KORE_HERMES_PROFILE", bridge.runtime_configuration_error())
        # Der Start haengt nicht mehr an KORE_HERMES_ALLOW_ARGV_PROMPTS: eine vollstaendige
        # Installation laeuft auf dem sicheren Standardweg los, ohne dass jemand eine
        # /proc-Preisgabe quittieren muss.
        with patch.object(bridge, "TOKEN", "configured"), patch.object(
            bridge, "ALLOW_ARGV_PROMPTS", False
        ), patch.object(bridge, "PLAN_ORIGIN_SECRET", "s" * 32), patch.object(
            bridge, "PLAN_ORIGIN_PRINCIPAL", "marius"
        ):
            self.assertIsNone(bridge.runtime_configuration_error())

    def test_reject_placeholder_names_the_key_never_the_value(self):
        for value in (None, "", "  ", "change-me", "change-me-bridge-token", "Change-Me-x"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError) as raised:
                    bridge.reject_placeholder("KORE_BRIDGE_TOKEN", value)
                self.assertIn("KORE_BRIDGE_TOKEN", str(raised.exception))
                if value and value.strip():
                    self.assertNotIn(value, str(raised.exception))
        self.assertIsNone(bridge.reject_placeholder("KORE_BRIDGE_TOKEN", "a" * 48))

    def test_runtime_configuration_rejects_example_token_and_origin_secret(self):
        complete = {
            "TOKEN": "configured",
            "PLAN_ORIGIN_SECRET": "s" * 32,
            "PLAN_ORIGIN_PRINCIPAL": "marius",
        }
        cases = (
            ("TOKEN", "change-me-bridge-token", "KORE_BRIDGE_TOKEN"),
            ("PLAN_ORIGIN_SECRET", "", "KORE_PLAN_ORIGIN_SECRET"),
            (
                "PLAN_ORIGIN_SECRET",
                "change-me-origin-secret-at-least-32-bytes-long",
                "KORE_PLAN_ORIGIN_SECRET",
            ),
            ("PLAN_ORIGIN_PRINCIPAL", "", "KORE_PLAN_ORIGIN_PRINCIPAL"),
        )
        for attribute, value, key in cases:
            config = {**complete, attribute: value}
            with self.subTest(key=key, value=value), patch.multiple(bridge, **config):
                error = bridge.runtime_configuration_error()
                self.assertIsNotNone(error)
                self.assertIn(key, error)
                if value:
                    self.assertNotIn(value, error)

    def test_bridge_process_refuses_to_start_with_example_values(self):
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "KORE_BRIDGE_TOKEN": "change-me-bridge-token",
            "KORE_BRIDGE_BIND_HOST": "127.0.0.1",
            "KORE_BRIDGE_PORT": "0",
            "KORE_INSTANCE_ID": "kore",
            "KORE_HERMES_PROFILE": "kore-coach",
            "KORE_PLAN_ORIGIN_PRINCIPAL": "kore",
            "KORE_PLAN_ORIGIN_SECRET": "change-me-origin-secret-at-least-32-bytes-long",
        }
        completed = subprocess.run(
            [sys.executable, os.path.join(os.path.dirname(bridge.__file__), "bridge.py")],
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("KORE_BRIDGE_TOKEN", completed.stderr)
        self.assertNotIn("change-me", completed.stderr + completed.stdout)

    def test_bind_host_accepts_only_concrete_loopback_or_rfc1918_ipv4_addresses(self):
        for address in (
            "127.0.0.1",
            "127.0.0.2",
            "10.0.0.4",
            "172.16.0.1",
            "172.31.255.254",
            "192.168.1.42",
        ):
            with self.subTest(address=address):
                self.assertEqual(bridge.validated_bind_host(address), address)

        for address in (
            "0.0.0.0",
            "::",
            "::1",
            "localhost",
            "kore-bridge.local",
            " 127.0.0.1",
            "127.0.0.1 ",
            "169.254.1.1",
            "192.0.2.1",
            "8.8.8.8",
            "224.0.0.1",
            "255.255.255.255",
            "not-an-address",
            "",
            None,
        ):
            with self.subTest(address=address):
                with self.assertRaises(ValueError):
                    bridge.validated_bind_host(address)

    def test_runtime_configuration_rejects_an_unsafe_bind_host(self):
        with patch.object(bridge, "TOKEN", "configured"), patch.object(
            bridge, "BIND_HOST", "0.0.0.0"
        ):
            self.assertIn("KORE_BRIDGE_BIND_HOST", bridge.runtime_configuration_error())

    @patch("bridge._run_process_bounded")
    def test_stderr_is_never_used_as_reply_or_session_source(self, run):
        # Bewusst neu gefasst (Spec C §10.1): Session und Antwort kommen nur aus dem
        # stdout-JSONL, maßgeblich aus dem result-Record. Hermes schreibt `session_id: <id>`
        # zusaetzlich auf stderr (stream_json.py) — diese Zeile und ein Provider-Geheimnis
        # daneben duerfen weder in `session` noch in `reply` landen.
        stderr = "╭\n│ provider secret: top-secret\n╰\nsession_id: leaked"
        run.side_effect = hermes_process(reply_stream("Antwort", "from-result"), stderr=stderr)

        result = bridge.run_turn(
            "Hallo", scope="general", conversation_id=CONVERSATION, session_ref=None,
            context=None, origin_ticket=None,
        )

        self.assertEqual(result.reply, "Antwort")
        self.assertEqual(bridge.verify_session(result.session, "general", CONVERSATION), "from-result")
        for value in (result.reply, result.session):
            self.assertNotIn("leaked", value)
            self.assertNotIn("top-secret", value)

        # Ohne stdout-JSONL gibt es keine Antwort und keine Session — stderr springt nicht ein.
        run.side_effect = hermes_process("", stderr=stderr)
        empty = bridge.run_turn(
            "Hallo", scope="general", conversation_id=CONVERSATION, session_ref=None,
            context=None, origin_ticket=None,
        )
        self.assertEqual(empty.error_code, "hermes_failed")
        self.assertIsNone(empty.session)
        self.assertNotIn("top-secret", empty.reply)
        self.assertNotIn("leaked", empty.reply)

    @patch("bridge._run_process_bounded")
    def test_nonzero_hermes_exit_does_not_surface_diagnostics(self, run):
        run.side_effect = hermes_process("", stderr="OPENROUTER_API_KEY=top-secret", returncode=7)

        result = bridge.run_turn(
            "Hallo", scope="general", conversation_id=CONVERSATION, session_ref=None,
            context=None, origin_ticket=None,
        )

        self.assertEqual(result.error_code, "hermes_failed")
        self.assertNotIn("top-secret", repr(result))

    def test_bounded_runner_rejects_excess_output(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(bridge.HermesOutputLimitExceeded):
                bridge._run_process_bounded(
                    [sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'x' * 8192)"],
                    cwd=directory,
                    env=bridge.hermes_child_env({}),
                    timeout=2,
                    output_limit=128,
                )

    def test_bounded_runner_times_out_and_reaps_the_process(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(subprocess.TimeoutExpired):
                bridge._run_process_bounded(
                    [sys.executable, "-c", "import time; time.sleep(5)"],
                    cwd=directory,
                    env=bridge.hermes_child_env({}),
                    timeout=0.05,
                    output_limit=4096,
                )

    def test_bounded_runner_reaps_descendant_after_leader_exits_with_open_pipes(self):
        descendant_pid = None
        with tempfile.TemporaryDirectory() as directory:
            pid_path = os.path.join(directory, "descendant.pid")
            parent_script = (
                "import subprocess, sys\n"
                "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
                "with open(sys.argv[1], 'w', encoding='utf-8') as handle:\n"
                "    handle.write(str(child.pid))\n"
            )
            try:
                with self.assertRaises(subprocess.TimeoutExpired):
                    bridge._run_process_bounded(
                        [sys.executable, "-c", parent_script, pid_path],
                        cwd=directory,
                        env=bridge.hermes_child_env({}),
                        timeout=0.5,
                        output_limit=4096,
                    )

                with open(pid_path, encoding="utf-8") as handle:
                    descendant_pid = int(handle.read())
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    try:
                        os.kill(descendant_pid, 0)
                    except ProcessLookupError:
                        break
                    time.sleep(0.01)
                else:
                    self.fail("Hermes descendant survived bounded-runner cleanup")
            finally:
                if descendant_pid is not None:
                    try:
                        os.kill(descendant_pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass

    def test_bounded_runner_delivers_stdin_verbatim_to_a_real_child(self):
        # Kein Mock: ein echter Prozess liest den Prompt von stdin zurueck. Hostile Zeichen
        # sind absichtlich dabei — auf stdin wird nichts von einer Shell interpretiert.
        prompt = 'Zeile 1\n"Anfuehrung" $(id) `date` \\ Umlaute: äöü\nZeile 2'
        script = (
            "import sys\n"
            "sys.stdout.write(sys.stdin.read())\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            result = bridge._run_process_bounded(
                [sys.executable, "-c", script],
                cwd=directory,
                env=bridge.hermes_child_env({}),
                timeout=10,
                output_limit=1_048_576,
                input_bytes=prompt.encode("utf-8"),
            )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, prompt)

    def test_bounded_runner_does_not_deadlock_on_a_prompt_larger_than_the_pipe_buffer(self):
        # Der eigentliche Grund fuer den Writer-Thread: 256 KiB passen nicht in den
        # 64-KiB-Pipe-Puffer. Ein blockierender Write im Hauptthread haette hier gehangen,
        # bis der Timeout zuschlaegt — und der Coach haette nie geantwortet.
        prompt = "ä" * 128_000  # 256 000 Bytes in UTF-8
        script = (
            "import sys\n"
            "data = sys.stdin.buffer.read()\n"
            "sys.stdout.write(str(len(data)))\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            result = bridge._run_process_bounded(
                [sys.executable, "-c", script],
                cwd=directory,
                env=bridge.hermes_child_env({}),
                timeout=20,
                output_limit=4096,
                input_bytes=prompt.encode("utf-8"),
            )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, str(len(prompt.encode("utf-8"))))

    def test_bounded_runner_survives_a_child_that_never_reads_stdin(self):
        # Ein Hermes, der frueh abbricht, laesst eine kaputte Pipe zurueck. Das darf den
        # Lauf nicht mit einem Writer-Fehler ueberschreiben — der Exit-Code redet.
        script = "import sys\nsys.stdout.write('fertig')\n"
        with tempfile.TemporaryDirectory() as directory:
            result = bridge._run_process_bounded(
                [sys.executable, "-c", script],
                cwd=directory,
                env=bridge.hermes_child_env({}),
                timeout=20,
                output_limit=4096,
                input_bytes=("x" * 256_000).encode("utf-8"),
            )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "fertig")

    def test_bounded_runner_reaps_descendant_that_closes_pipes_before_leader_exit(self):
        descendant_pid = None
        with tempfile.TemporaryDirectory() as directory:
            pid_path = os.path.join(directory, "closed-pipes-descendant.pid")
            ready_path = os.path.join(directory, "closed-pipes-descendant.ready")
            descendant_script = (
                "import os, sys, time\n"
                "os.close(1)\n"
                "os.close(2)\n"
                "with open(sys.argv[1], 'w', encoding='utf-8') as handle:\n"
                "    handle.write('ready')\n"
                "time.sleep(30)\n"
            )
            parent_script = (
                "import os, subprocess, sys, time\n"
                "child = subprocess.Popen([sys.executable, '-c', sys.argv[2], sys.argv[3]])\n"
                "with open(sys.argv[1], 'w', encoding='utf-8') as handle:\n"
                "    handle.write(str(child.pid))\n"
                "deadline = time.monotonic() + 1\n"
                "while not os.path.exists(sys.argv[3]) and time.monotonic() < deadline:\n"
                "    time.sleep(0.01)\n"
                "if not os.path.exists(sys.argv[3]):\n"
                "    raise SystemExit(2)\n"
            )
            try:
                result = bridge._run_process_bounded(
                    [
                        sys.executable,
                        "-c",
                        parent_script,
                        pid_path,
                        descendant_script,
                        ready_path,
                    ],
                    cwd=directory,
                    env=bridge.hermes_child_env({}),
                    timeout=2,
                    output_limit=4096,
                )
                self.assertEqual(result.returncode, 0)

                with open(pid_path, encoding="utf-8") as handle:
                    descendant_pid = int(handle.read())
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    try:
                        os.kill(descendant_pid, 0)
                    except ProcessLookupError:
                        break
                    time.sleep(0.01)
                else:
                    self.fail("Closed-pipes Hermes descendant survived successful cleanup")
            finally:
                if descendant_pid is not None:
                    try:
                        os.kill(descendant_pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass

    def test_request_and_session_contracts_reject_ambiguous_inputs(self):
        self.assertEqual(bridge.validated_content_length("128"), 128)
        with self.assertRaises(ValueError):
            bridge.validated_content_length("-1")
        with self.assertRaises(bridge.RequestTooLarge):
            bridge.validated_content_length(str(bridge.MAX_REQUEST_BYTES + 1))
        self.assertFalse(bridge.valid_scoped_session("health:--safe-mode"))
        self.assertFalse(bridge.valid_scoped_session("unscoped-session"))
        self.assertFalse(bridge.valid_scoped_session(42))
        self.assertIsNotNone(bridge.chat_contract_error({
            "message": 42,
            "health_context_allowed": False,
        }))
        self.assertIsNotNone(bridge.chat_contract_error({
            "message": "Hallo",
            "session": "health:--safe-mode",
            "health_context_allowed": True,
            "consent_scope_version": bridge.CONSENT_SCOPE_VERSION,
        }))
        self.assertIsNotNone(bridge.chat_contract_error({
            "message": "Hallo",
            "session": None,
            "health_context_allowed": False,
        }))
        self.assertIsNotNone(bridge.consent_contract_error({
            "health_context_allowed": True,
            "consent_scope_version": True,
            "context": {},
        }))
        self.assertIsNotNone(bridge.consent_contract_error({
            "health_context_allowed": True,
            "consent_scope_version": bridge.CONSENT_SCOPE_VERSION,
            "context": [],
        }))

    def test_scoped_session_boundaries_match_the_ios_contract(self):
        exact_raw = "a" * 120
        oversized_raw = "a" * 121

        self.assertTrue(bridge.valid_session_token(exact_raw))
        self.assertFalse(bridge.valid_session_token(oversized_raw))
        self.assertTrue(bridge.valid_scoped_session("general:" + exact_raw))
        self.assertEqual(len(("general:" + exact_raw).encode("ascii")), 128)
        self.assertTrue(bridge.valid_scoped_session("health:" + exact_raw))
        self.assertFalse(bridge.valid_scoped_session("general:" + oversized_raw))
        self.assertFalse(bridge.valid_scoped_session("other:" + exact_raw))
        self.assertFalse(bridge.valid_scoped_session("general:-invalid-first-byte"))
        # Die signierte Referenz nutzt dasselbe Format: <scope>:<hermes-id>.<22 Zeichen Signatur>.
        longest_hermes_id = "a" * (120 - 23)
        signed = bridge.sign_session("general", CONVERSATION, longest_hermes_id)
        self.assertTrue(bridge.valid_scoped_session(signed))
        self.assertEqual(len(signed.encode("ascii")), 128)
        self.assertEqual(
            bridge.verify_session(signed, "general", CONVERSATION), longest_hermes_id
        )
        self.assertIsNone(bridge.verify_session(signed, "health", CONVERSATION))

    def test_training_session_schema_rejects_boolean_and_nonfinite_numbers(self):
        valid = {
            "name": "Push",
            "exercises": [{
                "name": "Bankdrücken",
                "sets": [{"reps": 8, "weight_kg": 80.0}],
            }],
        }
        self.assertTrue(bridge.validate_session(valid))
        boolean_reps = {
            **valid,
            "exercises": [{"name": "Bankdrücken", "sets": [{"reps": True, "weight_kg": 80}]}],
        }
        infinite_weight = {
            **valid,
            "exercises": [{"name": "Bankdrücken", "sets": [{"reps": 8, "weight_kg": float("inf")}]}],
        }
        self.assertFalse(bridge.validate_session(boolean_reps))
        self.assertFalse(bridge.validate_session(infinite_weight))

    def test_training_session_schema_matches_ios_utf8_counts_and_unique_identity(self):
        exact = {
            "session_id": "123e4567-e89b-42d3-a456-426614174000",
            "name": "ä" * 64,
            "exercises": [{
                "exercise_id": "123e4567-e89b-42d3-a456-426614174001",
                "name": "ö" * 64,
                "note": "ü" * 256,
                "sets": [{"reps": 8, "reps_max": 12, "weight_kg": 1_000.0}] * 20,
            }],
        }
        self.assertTrue(bridge.validate_session(exact))

        too_long_name = {**exact, "name": "ä" * 65}
        self.assertFalse(bridge.validate_session(too_long_name))
        too_long_note = {
            **exact,
            "exercises": [{**exact["exercises"][0], "note": "ü" * 257}],
        }
        self.assertFalse(bridge.validate_session(too_long_note))
        too_many_exercises = {
            **exact,
            "exercises": [
                {**exact["exercises"][0], "name": f"Exercise {index}"}
                for index in range(33)
            ],
        }
        self.assertFalse(bridge.validate_session(too_many_exercises))
        duplicates = {
            **exact,
            "exercises": [
                {**exact["exercises"][0], "name": " Squat "},
                {**exact["exercises"][0], "name": "sQuAt\n"},
            ],
        }
        self.assertFalse(bridge.validate_session(duplicates))
        invalid_range = {
            **exact,
            "exercises": [{
                **exact["exercises"][0],
                "sets": [{"reps": 8, "reps_max": 8, "weight_kg": 80}],
            }],
        }
        self.assertFalse(bridge.validate_session(invalid_range))

        self.assertIn("feste Wiederholungen dürfen nur sinken", bridge.ADJUST_INSTRUCTION)
        self.assertIn("reps/reps_max-Bereiche", bridge.ADJUST_INSTRUCTION)

    def test_json_parser_rejects_duplicate_keys_and_nonfinite_numbers(self):
        with self.assertRaises(ValueError):
            bridge.strict_json_loads(
                b'{"health_context_allowed":false,"health_context_allowed":true}'
            )
        with self.assertRaises(ValueError):
            bridge.strict_json_loads(b'{"readiness":NaN}')


if __name__ == "__main__":
    unittest.main()
