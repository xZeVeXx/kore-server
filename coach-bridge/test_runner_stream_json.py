"""Bridge-Runner 0.10: stream-json, Session aus dem result-Record, signierte Referenz, -t-Allowlist.

Echte Hermes-Ausgaben liegen in fixtures/hermes-v0.21.4/; Dateien mit dem Praefix
`synthetic-` sind nachgebaut und im README dort so markiert.
"""
import base64
import hashlib
import hmac
import http.client
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import bridge

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "hermes-v0.21.4"
FIXTURE_SESSION = "20260924_085115_defa4e"
CONVERSATION = "123e4567-e89b-42d3-a456-426614174000"
OTHER_CONVERSATION = "123e4567-e89b-42d3-a456-426614174999"
TEST_TOKEN = "t" * 48


def fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def stream_lines(*records) -> str:
    return "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records)


def result_record(text="Antwort", session_id="20260924_120000_aaaaaa", exit_code=0, **extra):
    return {"type": "result", "session_id": session_id, "exit_code": exit_code, "text": text, **extra}


def reply_stream(text="Antwort", session_id="20260924_120000_aaaaaa") -> str:
    """Kleinster gueltiger Lauf: init, ein Delta, result."""
    return stream_lines(
        {"type": "system", "subtype": "init", "session_id": session_id},
        {"type": "text", "text": text},
        result_record(text, session_id),
    )


def hermes_process(stdout: str, stderr: str = "", returncode: int = 0):
    """side_effect fuer _run_process_bounded: speist stdout zeilenweise in den Callback wie der
    echte Reader und liefert danach das CompletedProcess-aehnliche Ergebnis."""
    def run(args, **kwargs):
        callback = kwargs.get("on_stdout_line")
        if callback is not None:
            for line in stdout.splitlines():
                callback(line)
        return SimpleNamespace(args=args, stdout=stdout, stderr=stderr, returncode=returncode)
    return run


def hermes_sequence(*runs):
    """Mehrere Laeufe hintereinander (z. B. Resume scheitert, dann neuer Lauf)."""
    pending = list(runs)

    def run(args, **kwargs):
        return pending.pop(0)(args, **kwargs)
    return run


def arg_value(args, flag):
    return args[args.index(flag) + 1]


class StreamJsonRunnerTests(unittest.TestCase):
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

    # ---- stream-json lesen ---------------------------------------------------------------

    def test_session_and_reply_come_from_the_result_record_of_a_real_run(self):
        parsed = bridge.parse_stream_json(fixture_text("stream-general.jsonl").splitlines())

        self.assertEqual(parsed.deltas, ["OK"])
        self.assertEqual(parsed.reply, "OK")
        self.assertEqual(parsed.session_id, FIXTURE_SESSION)
        self.assertFalse(parsed.used_kore)
        self.assertIsNone(parsed.error_text)

    def test_result_session_wins_over_init_session_synthetic_compression(self):
        parsed = bridge.parse_stream_json(
            fixture_text("synthetic-stream-compressed.jsonl").splitlines()
        )

        self.assertEqual(parsed.session_id, "20260924_091500_c0ffee")
        self.assertEqual(parsed.deltas, ["O", "K"])
        self.assertEqual(parsed.reply, "OK")

    def test_tool_records_never_reach_reply_or_deltas_but_mark_used_kore(self):
        parsed = bridge.parse_stream_json(
            fixture_text("synthetic-stream-tool-use.jsonl").splitlines()
        )

        visible = "".join(parsed.deltas) + (parsed.reply or "")
        self.assertEqual(parsed.reply, "Plan vorgeschlagen.")
        self.assertNotIn("SYNTHETIC-TOOL-INPUT-TICKET", visible)
        self.assertNotIn("SYNTHETIC-TOOL-OUTPUT", visible)
        self.assertNotIn("propose_training_plan", visible)
        self.assertTrue(parsed.used_kore)

    def test_parser_ignores_noise_and_rejects_unsafe_session_ids(self):
        lines = [
            "Initializing agent...",
            "session_id: from-stdout-text",
            "{not json",
            json.dumps(["not", "an", "object"]),
            json.dumps({"type": "reasoning", "text": "intern"}),
            json.dumps({"type": "text", "text": "Hallo"}),
            json.dumps(result_record("Hallo", session_id="--safe-mode")),
        ]

        parsed = bridge.parse_stream_json(lines)

        self.assertEqual(parsed.deltas, ["Hallo"])
        self.assertEqual(parsed.reply, "Hallo")
        self.assertIsNone(parsed.session_id)

    def test_missing_result_record_yields_no_reply_and_no_session(self):
        parsed = bridge.parse_stream_json([json.dumps({"type": "text", "text": "halb"})])

        self.assertIsNone(parsed.reply)
        self.assertIsNone(parsed.session_id)
        self.assertIsNone(parsed.result_record)

    def test_unknown_session_is_classified_only_by_the_exact_stderr_line(self):
        record = bridge.parse_stream_json(
            fixture_text("stream-resume-unknown.jsonl").splitlines()
        ).result_record
        stderr = fixture_text("stream-resume-unknown.stderr.txt")

        self.assertEqual(record["error"], "credentials or agent init failed")
        self.assertTrue(bridge.is_unknown_session_error(record, stderr, FIXTURE_SESSION))
        # Derselbe result-Text ohne die stderr-Zeile ist eine gescheiterte Anmeldung.
        self.assertFalse(bridge.is_unknown_session_error(record, "", FIXTURE_SESSION))
        self.assertFalse(bridge.is_unknown_session_error(
            record, "auth failed: credentials or agent init failed\n", FIXTURE_SESSION
        ))
        # Nur die Session, die die Bridge selbst fortsetzen wollte, und nur als ganze Zeile.
        self.assertFalse(bridge.is_unknown_session_error(record, stderr, "20260924_000000_other1"))
        self.assertFalse(bridge.is_unknown_session_error(
            record, f"note: Session not found: {FIXTURE_SESSION}\n", FIXTURE_SESSION
        ))
        # Ein erfolgreicher Lauf ist nie „unbekannt", auch wenn stderr die Zeile traegt.
        ok = bridge.parse_stream_json(fixture_text("stream-general.jsonl").splitlines())
        self.assertFalse(bridge.is_unknown_session_error(ok.result_record, stderr, FIXTURE_SESSION))

    # ---- Werkzeuglisten und Argumente --------------------------------------------------------

    def test_toolsets_are_a_positive_allowlist_without_shell_file_or_session_search(self):
        self.assertEqual(bridge.APP_CHAT_TOOLSETS["general"], ("web", "skills", "memory"))
        # 0.13 (Entscheidung 11.10.): kein web im Health-Scope — sonst koennte ein Health-Turn den
        # Gesundheitskontext ueber web_extract/web_search an eine fremde Adresse tragen.
        self.assertEqual(bridge.APP_CHAT_TOOLSETS["health"], ("skills", "memory", "kore"))
        self.assertEqual(
            bridge.FORBIDDEN_TOOLSETS,
            ("terminal", "file", "code_execution", "cronjob", "computer_use", "browser"),
        )
        for scope, toolsets in bridge.APP_CHAT_TOOLSETS.items():
            with self.subTest(scope=scope):
                self.assertFalse(set(toolsets) & set(bridge.FORBIDDEN_TOOLSETS))
                self.assertFalse(any(name.startswith("hermes-") for name in toolsets))
                self.assertNotIn("session_search", toolsets)
        self.assertNotIn("kore", bridge.APP_CHAT_TOOLSETS["general"])

    def test_kore_toolset_only_in_health_scope_with_origin_ticket(self):
        self.assertEqual(bridge.toolsets_for("general", False), ("web", "skills", "memory"))
        self.assertEqual(bridge.toolsets_for("general", True), ("web", "skills", "memory"))
        # Ohne Ticket (z. B. Tages-Anpassung) bleibt der Health-Scope ohne kore UND ohne web.
        self.assertEqual(bridge.toolsets_for("health", False), ("skills", "memory"))
        self.assertEqual(bridge.toolsets_for("health", True), ("skills", "memory", "kore"))
        with self.assertRaises(ValueError):
            bridge.toolsets_for("other", True)

    def test_hermes_args_match_the_interface_exactly_for_any_valid_profile(self):
        for profile in ("kore-coach", "my-coach", "Trainer.2"):
            with self.subTest(profile=profile):
                args = bridge.hermes_args(
                    profile=profile,
                    scope="general",
                    toolsets=("web", "skills", "memory"),
                    run_budget_s=135,
                    max_turns="6",
                    resume_id=None,
                )
                self.assertEqual(args, [
                    "/opt/hermes/bin/hermes", "-p", profile, "chat", "-Q",
                    "--format", "stream-json", "--query-file", "-", "--max-turns", "6",
                    "--source", "kore-general", "-t", "web,skills,memory", "--run-budget", "135",
                ])

        resumed = bridge.hermes_args(
            profile="kore-coach",
            scope="health",
            toolsets=("skills", "memory", "kore"),
            run_budget_s=135,
            max_turns="4",
            resume_id=FIXTURE_SESSION,
        )
        self.assertEqual(resumed[-2:], ["--resume", FIXTURE_SESSION])
        self.assertEqual(arg_value(resumed, "--source"), "kore-health")
        self.assertEqual(arg_value(resumed, "-t"), "skills,memory,kore")
        for forbidden in ("--safe-mode", "--ignore-rules", "-q", "--query"):
            self.assertNotIn(forbidden, resumed)

    def test_hermes_args_refuse_unsafe_values(self):
        base = {
            "profile": "kore-coach",
            "scope": "general",
            "toolsets": ("web",),
            "run_budget_s": 135,
            "max_turns": "6",
            "resume_id": None,
        }
        for label, override in (
            ("profile flag", {"profile": "--safe-mode"}),
            ("empty profile", {"profile": ""}),
            ("scope", {"scope": "tool"}),
            ("terminal", {"toolsets": ("web", "terminal")}),
            ("file", {"toolsets": ("file",)}),
            ("hermes bundle", {"toolsets": ("hermes-cli",)}),
            ("session_search", {"toolsets": ("session_search",)}),
            ("no toolsets", {"toolsets": ()}),
            ("web im Health-Scope", {"scope": "health", "toolsets": ("web", "skills", "memory")}),
            ("resume flag", {"resume_id": "--yolo"}),
            ("max turns", {"max_turns": "13"}),
            ("budget", {"run_budget_s": 0}),
        ):
            with self.subTest(label), self.assertRaises(ValueError):
                bridge.hermes_args(**{**base, **override})

    # ---- signierte Session-Referenz --------------------------------------------------------

    def test_signed_reference_follows_the_documented_hmac_construction(self):
        key = hmac.new(TEST_TOKEN.encode(), b"kore-conversation-session-v1", hashlib.sha256).digest()
        mac = hmac.new(
            key, "\x00".join(("general", CONVERSATION, FIXTURE_SESSION)).encode(), hashlib.sha256
        ).digest()
        signature = base64.urlsafe_b64encode(mac).rstrip(b"=").decode("ascii")[:22]

        ref = bridge.sign_session("general", CONVERSATION, FIXTURE_SESSION)

        self.assertEqual(ref, f"general:{FIXTURE_SESSION}.{signature}")
        self.assertTrue(bridge.valid_scoped_session(ref))
        self.assertLessEqual(len(ref.partition(":")[2]), 120)
        self.assertRegex(ref.partition(":")[2], r"\A[A-Za-z0-9._~-]+\Z")
        self.assertEqual(bridge.verify_session(ref, "general", CONVERSATION), FIXTURE_SESSION)

    def test_foreign_chat_scope_signature_or_token_never_verifies(self):
        ref = bridge.sign_session("health", CONVERSATION, FIXTURE_SESSION)
        head, _, signature = ref.rpartition(".")
        flipped = head + "." + ("A" if signature[0] != "A" else "B") + signature[1:]

        self.assertIsNone(bridge.verify_session(ref, "health", OTHER_CONVERSATION))
        self.assertIsNone(bridge.verify_session(ref, "general", CONVERSATION))
        self.assertIsNone(bridge.verify_session(
            "general:" + ref.partition(":")[2], "general", CONVERSATION
        ))
        self.assertIsNone(bridge.verify_session(flipped, "health", CONVERSATION))
        self.assertIsNone(bridge.verify_session(f"health:{FIXTURE_SESSION}", "health", CONVERSATION))
        self.assertIsNone(bridge.verify_session("health:--safe-mode", "health", CONVERSATION))
        with patch.object(bridge, "TOKEN", "u" * 48):
            self.assertIsNone(bridge.verify_session(ref, "health", CONVERSATION))

    def test_session_id_too_long_for_the_app_format_is_not_signed(self):
        longest = "a" * (120 - 23)
        self.assertTrue(bridge.valid_scoped_session(bridge.sign_session("general", CONVERSATION, longest)))
        with self.assertRaises(ValueError):
            bridge.sign_session("general", CONVERSATION, longest + "a")

    # ---- run_turn ---------------------------------------------------------------------------

    @patch("bridge._run_process_bounded")
    def test_new_general_turn_streams_deltas_and_returns_a_signed_session(self, run):
        run.side_effect = hermes_process(
            fixture_text("stream-general.jsonl"), stderr=f"\nsession_id: {FIXTURE_SESSION}\n"
        )
        deltas = []

        outcome = bridge.run_turn(
            "Antworte nur mit OK",
            scope="general",
            conversation_id=CONVERSATION,
            session_ref=None,
            context=None,
            origin_ticket=None,
            on_delta=deltas.append,
        )

        args = run.call_args.args[0]
        self.assertEqual(args, bridge.hermes_args(
            profile="kore-coach", scope="general", toolsets=("web", "skills", "memory"),
            run_budget_s=135, max_turns="6", resume_id=None,
        ))
        self.assertEqual(run.call_args.kwargs["timeout"], 150)
        self.assertEqual(run.call_args.kwargs["output_limit"], bridge.MAX_OUTPUT_BYTES)
        self.assertEqual(deltas, ["OK"])
        self.assertEqual(outcome.reply, "OK")
        self.assertEqual(outcome.context, "new")
        self.assertIsNone(outcome.reset_reason)
        self.assertIsNone(outcome.error_code)
        self.assertFalse(outcome.used_kore)
        self.assertEqual(bridge.verify_session(outcome.session, "general", CONVERSATION), FIXTURE_SESSION)

    @patch("bridge._run_process_bounded")
    def test_stderr_session_line_and_provider_secret_never_reach_the_app(self, run):
        run.side_effect = hermes_process(
            reply_stream("Antwort", "20260924_120000_aaaaaa"),
            stderr="provider secret: top-secret\nsession_id: leaked-session\n",
        )

        outcome = bridge.run_turn(
            "Hallo", scope="general", conversation_id=CONVERSATION, session_ref=None,
            context=None, origin_ticket=None,
        )

        self.assertEqual(
            bridge.verify_session(outcome.session, "general", CONVERSATION), "20260924_120000_aaaaaa"
        )
        for value in (outcome.reply, outcome.session):
            self.assertNotIn("leaked-session", value)
            self.assertNotIn("top-secret", value)

    @patch("bridge._run_process_bounded")
    def test_signed_reference_resumes_the_hermes_session(self, run):
        run.side_effect = hermes_process(fixture_text("stream-general-resumed.jsonl"))
        ref = bridge.sign_session("general", CONVERSATION, FIXTURE_SESSION)

        outcome = bridge.run_turn(
            "Weiter", scope="general", conversation_id=CONVERSATION, session_ref=ref,
            context=None, origin_ticket=None,
        )

        self.assertEqual(arg_value(run.call_args.args[0], "--resume"), FIXTURE_SESSION)
        self.assertEqual(outcome.context, "continued")
        self.assertIsNone(outcome.reset_reason)
        self.assertEqual(outcome.session, ref)

    @patch("bridge._run_process_bounded")
    def test_invalid_references_never_resume_and_report_reset_invalid(self, run):
        run.side_effect = hermes_process(reply_stream())
        good = bridge.sign_session("general", CONVERSATION, FIXTURE_SESSION)
        head, _, signature = good.rpartition(".")
        cases = {
            "foreign conversation": bridge.sign_session("general", OTHER_CONVERSATION, FIXTURE_SESSION),
            "foreign scope": bridge.sign_session("health", CONVERSATION, FIXTURE_SESSION),
            "manipulated signature": head + "." + signature[::-1],
            "unsigned legacy": f"general:{FIXTURE_SESSION}",
        }
        for label, ref in cases.items():
            with self.subTest(label):
                run.reset_mock()
                outcome = bridge.run_turn(
                    "Hallo", scope="general", conversation_id=CONVERSATION, session_ref=ref,
                    context=None, origin_ticket=None,
                )
                run.assert_called_once()
                self.assertNotIn("--resume", run.call_args.args[0])
                self.assertEqual((outcome.context, outcome.reset_reason), ("reset", "invalid"))
                self.assertIsNone(outcome.error_code)
                self.assertEqual(
                    bridge.verify_session(outcome.session, "general", CONVERSATION),
                    "20260924_120000_aaaaaa",
                )

    @patch("bridge._run_process_bounded")
    def test_unknown_session_starts_a_fresh_run_in_the_same_turn(self, run):
        run.side_effect = hermes_sequence(
            hermes_process(
                fixture_text("stream-resume-unknown.jsonl"),
                stderr=fixture_text("stream-resume-unknown.stderr.txt"),
                returncode=1,
            ),
            hermes_process(reply_stream("Neu begonnen", "20260924_130000_bbbbbb")),
        )
        ref = bridge.sign_session("general", CONVERSATION, FIXTURE_SESSION)
        deltas = []

        outcome = bridge.run_turn(
            "Weiter", scope="general", conversation_id=CONVERSATION, session_ref=ref,
            context=None, origin_ticket=None, on_delta=deltas.append,
        )

        self.assertEqual(run.call_count, 2)
        first, second = (call.args[0] for call in run.call_args_list)
        self.assertEqual(arg_value(first, "--resume"), FIXTURE_SESSION)
        self.assertNotIn("--resume", second)
        self.assertEqual((outcome.context, outcome.reset_reason), ("reset", "expired"))
        self.assertIsNone(outcome.error_code)
        self.assertEqual(outcome.reply, "Neu begonnen")
        self.assertEqual(deltas, ["Neu begonnen"])
        self.assertEqual(
            bridge.verify_session(outcome.session, "general", CONVERSATION), "20260924_130000_bbbbbb"
        )
        for value in (outcome.reply, outcome.session):
            self.assertNotIn("Session not found", value)
            self.assertNotIn("credentials", value)

    @patch("bridge._run_process_bounded")
    def test_init_failure_without_the_stderr_line_is_hermes_failed_without_retry(self, run):
        run.side_effect = hermes_process(
            fixture_text("stream-resume-unknown.jsonl"),
            stderr="OPENROUTER_API_KEY=top-secret\n",
            returncode=1,
        )
        ref = bridge.sign_session("general", CONVERSATION, FIXTURE_SESSION)

        outcome = bridge.run_turn(
            "Weiter", scope="general", conversation_id=CONVERSATION, session_ref=ref,
            context=None, origin_ticket=None,
        )

        run.assert_called_once()
        self.assertEqual(outcome.error_code, "hermes_failed")
        self.assertIsNone(outcome.session)
        self.assertNotIn("top-secret", outcome.reply)
        self.assertNotIn("credentials", outcome.reply)

    @patch("bridge._run_process_bounded")
    def test_health_scope_uses_kore_only_with_a_ticket_and_never_safe_mode(self, run):
        run.side_effect = hermes_process(reply_stream())
        for ticket, expected in ((None, "skills,memory"), ("tkt.sig", "skills,memory,kore")):
            with self.subTest(ticket=ticket):
                bridge.run_turn(
                    "Wie steht es?", scope="health", conversation_id=CONVERSATION,
                    session_ref=None, context=None, origin_ticket=ticket,
                )
                args = run.call_args.args[0]
                self.assertEqual(arg_value(args, "-t"), expected)
                self.assertEqual(arg_value(args, "--source"), "kore-health")
                self.assertNotIn("--safe-mode", args)
                self.assertNotIn("--ignore-rules", args)
        with self.assertRaises(ValueError):
            bridge.run_turn(
                "Hallo", scope="general", conversation_id=CONVERSATION, session_ref=None,
                context=None, origin_ticket="tkt.sig",
            )

    @patch("bridge._run_process_bounded")
    def test_used_kore_comes_from_tool_use_names_only_in_health_scope(self, run):
        run.side_effect = hermes_process(fixture_text("synthetic-stream-tool-use.jsonl"))

        health = bridge.run_turn(
            "Plan?", scope="health", conversation_id=CONVERSATION, session_ref=None,
            context=None, origin_ticket="tkt.sig",
        )
        general = bridge.run_turn(
            "Plan?", scope="general", conversation_id=CONVERSATION, session_ref=None,
            context=None, origin_ticket=None,
        )

        self.assertTrue(health.used_kore)
        self.assertFalse(general.used_kore)
        self.assertNotIn("SYNTHETIC-TOOL", health.reply)

    @patch("bridge._run_process_bounded")
    def test_runtime_failures_return_error_codes_with_redacted_partial_text(self, run):
        cases = (
            (subprocess.TimeoutExpired(["hermes"], 150), "timeout"),
            (bridge.HermesOutputLimitExceeded("too much"), "output_limit"),
            (bridge.HermesInvocationError("reader"), "hermes_failed"),
        )
        for error, code in cases:
            with self.subTest(code=code):
                def failing(args, error=error, **kwargs):
                    kwargs["on_stdout_line"](json.dumps({"type": "text", "text": "Teil tkt.sig"}))
                    raise error
                run.side_effect = failing
                outcome = bridge.run_turn(
                    "Hallo", scope="health", conversation_id=CONVERSATION, session_ref=None,
                    context=None, origin_ticket="tkt.sig",
                )
                self.assertEqual(outcome.error_code, code)
                self.assertEqual(outcome.reply, "Teil [REDACTED]")
                self.assertIsNone(outcome.session)

    @patch("bridge._run_process_bounded")
    def test_argv_opt_in_fails_closed_because_the_turn_path_is_stdin_only(self, run):
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
    def test_run_budget_stays_fifteen_seconds_below_the_bridge_timeout(self, run):
        run.side_effect = hermes_process(reply_stream())
        with patch.object(bridge, "TIMEOUT", 60):
            bridge.run_turn(
                "Hallo", scope="general", conversation_id=CONVERSATION, session_ref=None,
                context=None, origin_ticket=None,
            )
        self.assertEqual(arg_value(run.call_args.args[0], "--run-budget"), "45")

    # ---- zeilenweiser Prozess-Reader ------------------------------------------------------

    def test_bounded_runner_delivers_stdout_lines_while_the_process_still_runs(self):
        # Das Kind wartet nach der ersten Zeile, bis der Callback sie gesehen hat. Liefert der
        # Reader erst am Prozessende, laeuft der Test in den Timeout.
        with tempfile.TemporaryDirectory() as directory:
            marker = os.path.join(directory, "seen")
            script = (
                "import os, sys, time\n"
                "sys.stdout.write('{\"type\": \"text\", \"text\": \"a\"}\\n'); sys.stdout.flush()\n"
                "deadline = time.monotonic() + 5\n"
                "while not os.path.exists(sys.argv[1]) and time.monotonic() < deadline:\n"
                "    time.sleep(0.01)\n"
                "sys.stdout.write('zweite' if os.path.exists(sys.argv[1]) else 'zu-spaet')\n"
            )
            lines = []

            def on_line(line):
                lines.append(line)
                if len(lines) == 1:
                    Path(marker).touch()

            started = time.monotonic()
            result = bridge._run_process_bounded(
                [sys.executable, "-c", script, marker],
                cwd=directory,
                env=bridge.hermes_child_env({}),
                timeout=10,
                output_limit=4096,
                on_stdout_line=on_line,
            )
        self.assertLess(time.monotonic() - started, 4)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(lines, ['{"type": "text", "text": "a"}', "zweite"])

    def test_bounded_runner_keeps_the_output_limit_with_a_line_callback(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(bridge.HermesOutputLimitExceeded):
                bridge._run_process_bounded(
                    [sys.executable, "-c", "import sys; sys.stdout.write('x\\n' * 8192)"],
                    cwd=directory,
                    env=bridge.hermes_child_env({}),
                    timeout=5,
                    output_limit=128,
                    on_stdout_line=lambda _line: None,
                )

    def test_bounded_runner_reports_the_started_process(self):
        seen = []
        with tempfile.TemporaryDirectory() as directory:
            bridge._run_process_bounded(
                [sys.executable, "-c", "pass"],
                cwd=directory,
                env=bridge.hermes_child_env({}),
                timeout=5,
                output_limit=128,
                on_process=seen.append,
            )
        self.assertEqual(len(seen), 1)
        self.assertIsInstance(seen[0].pid, int)

    # ---- Vertrag nach aussen --------------------------------------------------------------

    def test_capabilities_and_health_report_the_new_contract(self):
        capabilities = bridge.capabilities_payload()["capabilities"]
        health = bridge.health_payload()

        self.assertIn("conversation_sessions_v2", capabilities)
        self.assertIn("app_chat_toolsets_v1", capabilities)
        self.assertNotIn("safe_mode_without_health_v1", capabilities)
        # `health` bleibt der Standard ohne web; `health_with_web` ist die Liste fuer den
        # ausdruecklichen Wunsch (health_web_allowed, 0.13 S13).
        self.assertEqual(health["app_chat_toolsets"], {
            "general": ["web", "skills", "memory"],
            "health": ["skills", "memory", "kore"],
            "health_with_web": ["web", "skills", "memory", "kore"],
        })

    def post(self, path, payload):
        body = json.dumps(payload).encode("utf-8")
        handler = object.__new__(bridge.Handler)
        handler.path = path
        handler.headers = http.client.HTTPMessage()  # wie im Betrieb; ein dict hat kein get_all
        handler.headers["Authorization"] = f"Bearer {TEST_TOKEN}"
        handler.headers["Content-Length"] = str(len(body))
        handler.rfile = io.BytesIO(body)
        sent = []
        handler._send = lambda code, obj: sent.append((code, obj))
        handler.do_POST()
        return sent[0]

    @patch("bridge._run_process_bounded")
    def test_synchronous_coach_path_returns_json_with_a_signed_session(self, run):
        run.side_effect = hermes_process(
            fixture_text("stream-general.jsonl"), stderr="session_id: leaked\n"
        )

        code, payload = self.post("/coach", {
            "message": "Antworte nur mit OK",
            "conversation_id": CONVERSATION,
            "health_context_allowed": False,
        })

        self.assertEqual(code, 200)
        self.assertEqual(payload["reply"], "OK")
        self.assertFalse(payload["used_kore"])
        self.assertEqual(payload["context"], "new")
        self.assertNotIn("reset_reason", payload)
        self.assertEqual(bridge.verify_session(payload["session"], "general", CONVERSATION), FIXTURE_SESSION)
        self.assertNotIn("leaked", json.dumps(payload))

    @patch("bridge._run_process_bounded")
    def test_synchronous_coach_path_maps_turn_errors_without_diagnostics(self, run):
        run.side_effect = hermes_process("", stderr="provider secret: top-secret", returncode=3)

        code, payload = self.post("/coach", {
            "message": "Hallo",
            "conversation_id": CONVERSATION,
            "health_context_allowed": False,
        })

        self.assertEqual(code, 502)
        self.assertEqual(payload, {"error": "Dein Coach konnte keine sichere Antwort liefern."})

    @patch("bridge._run_process_bounded")
    def test_adjust_today_runs_in_the_health_scope_with_the_health_scope_toolsets(self, run):
        session = {"name": "Push", "exercises": [{"name": "Bank", "sets": [{"reps": 8, "weight_kg": 80}]}]}
        answer = json.dumps({"session": session, "rationale": "kurz", "changes": []})
        run.side_effect = hermes_process(reply_stream(answer))

        code, payload = self.post("/coach/adjust-today", {
            "session": session,
            "health_context_allowed": True,
            "consent_scope_version": bridge.CONSENT_SCOPE_VERSION,
            "context": {"readiness": 70},
        })

        self.assertEqual(code, 200)
        self.assertEqual(payload["session"], session)
        args = run.call_args.args[0]
        self.assertEqual(arg_value(args, "--source"), "kore-health")
        self.assertEqual(arg_value(args, "-t"), ",".join(bridge.toolsets_for("health", False)))
        self.assertNotIn("web", arg_value(args, "-t").split(","))
        self.assertEqual(arg_value(args, "--max-turns"), "4")
        self.assertEqual(arg_value(args, "-p"), "kore-coach")
        self.assertNotIn("--safe-mode", args)
        self.assertNotIn("--resume", args)


if __name__ == "__main__":
    unittest.main()
