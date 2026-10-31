"""0.14.1: Die Bridge passt sich an die Kommandozeile des installierten Hermes an, statt Optionen zu erzwingen."""

import io
import unittest
from contextlib import redirect_stderr
from unittest.mock import patch

import bridge
import test_runner_stream_json as stream_tests
from test_runner_stream_json import CONVERSATION, hermes_process, hermes_sequence, reply_stream, stream_lines

# Wortlaut von Hermes v0.22.5 im Gateway-Modus (result-Record und stderr), gekuerzt um den Hinweistext.
GATEWAY_ERROR = (
    "Unsupported gateway CLI options: --run-budget. No local fallback or policy changes were made.\n"
    "  --run-budget: use `agent.run_budget_seconds` in config.yaml"
)


def rejected_stream(error=GATEWAY_ERROR) -> str:
    return stream_lines(
        {"type": "system", "subtype": "init", "model": "", "session_id": ""},
        {"type": "result", "session_id": "", "exit_code": 2, "text": "", "error": error},
    )


def rejected_process(error=GATEWAY_ERROR):
    return hermes_process(rejected_stream(error), stderr=f"Error: {error}\n", returncode=2)


class HermesOptionAdaptationTests(unittest.TestCase):
    def setUp(self):
        # Dieselbe Bridge-Umgebung wie die Stream-json-Tests, ohne deren Tests hier ein zweites Mal zu sammeln.
        stream_tests.StreamJsonRunnerTests.setUp(self)
        bridge._dropped_hermes_options.clear()
        self.addCleanup(bridge._dropped_hermes_options.clear)

    def turn(self):
        with redirect_stderr(io.StringIO()) as journal:
            outcome = bridge.run_turn(
                "Hallo", scope="general", conversation_id=CONVERSATION, session_ref=None,
                context=None, origin_ticket=None,
            )
        return outcome, journal.getvalue()

    def test_the_gateway_message_names_exactly_the_rejected_options(self):
        self.assertEqual(bridge.unsupported_hermes_options({"error": GATEWAY_ERROR}, None),
                         frozenset({"--run-budget"}))
        self.assertEqual(bridge.unsupported_hermes_options(None, f"Error: {GATEWAY_ERROR}\n"),
                         frozenset({"--run-budget"}))
        self.assertEqual(
            bridge.unsupported_hermes_options({"error": "Unsupported CLI options: --run-budget, --max-turns."}, None),
            frozenset({"--run-budget", "--max-turns"}),
        )
        self.assertEqual(bridge.unsupported_hermes_options({"error": "credentials or agent init failed"}, "boom"),
                         frozenset())

    def test_dropping_an_option_removes_its_value_but_never_the_program(self):
        args = ["/opt/hermes", "chat", "--max-turns", "6", "--run-budget", "135", "-t", "web"]
        self.assertEqual(bridge.without_hermes_options(args, {"--run-budget"}),
                         ["/opt/hermes", "chat", "--max-turns", "6", "-t", "web"])
        self.assertEqual(bridge.without_hermes_options(["--run-budget", "x"], {"--run-budget"}), ["--run-budget", "x"])

    @patch("bridge._run_process_bounded")
    def test_a_rejected_run_budget_is_dropped_and_the_turn_answers(self, run):
        run.side_effect = hermes_sequence(rejected_process(), hermes_process(reply_stream("Hallo zurück")))

        outcome, journal = self.turn()

        self.assertIsNone(outcome.error_code)
        self.assertEqual(outcome.reply, "Hallo zurück")
        self.assertEqual(run.call_count, 2)
        first, second = (call.args[0] for call in run.call_args_list)
        self.assertIn("--run-budget", first)
        self.assertNotIn("--run-budget", second)
        self.assertEqual(second, bridge.without_hermes_options(first, {"--run-budget"}))
        self.assertIn("kore-hermes-option-dropped option=--run-budget", journal)

    @patch("bridge._run_process_bounded")
    def test_a_learned_drop_applies_from_the_start_of_later_turns(self, run):
        run.side_effect = hermes_sequence(rejected_process(), hermes_process(reply_stream()),
                                          hermes_process(reply_stream()))
        self.turn()

        outcome, journal = self.turn()

        self.assertIsNone(outcome.error_code)
        self.assertEqual(run.call_count, 3)
        self.assertNotIn("--run-budget", run.call_args_list[2].args[0])
        self.assertEqual(journal, "", "Die Anpassung steht nur einmal im Journal.")

    @patch("bridge._run_process_bounded")
    def test_a_rejected_safety_option_is_never_dropped(self, run):
        error = "Unsupported gateway CLI options: -t. No local fallback or policy changes were made."
        run.side_effect = hermes_sequence(rejected_process(error))

        outcome, _ = self.turn()

        self.assertEqual(outcome.error_code, "hermes_failed")
        self.assertEqual(outcome.failure_class, "hermes_option_unsupported")
        self.assertEqual(run.call_count, 1, "Werkzeugliste weglassen hiesse mehr Werkzeuge: kein zweiter Lauf.")
        self.assertEqual(bridge._dropped_hermes_options, set())

    @patch("bridge._run_process_bounded")
    def test_a_mixed_rejection_with_a_safety_option_fails_without_dropping_anything(self, run):
        error = "Unsupported gateway CLI options: --run-budget, --query-file. No local fallback."
        run.side_effect = hermes_sequence(rejected_process(error))

        outcome, _ = self.turn()

        self.assertEqual(outcome.error_code, "hermes_failed")
        self.assertEqual(run.call_count, 1)
        self.assertEqual(bridge._dropped_hermes_options, set())

    @patch("bridge._run_process_bounded")
    def test_a_second_rejection_after_the_retry_does_not_loop(self, run):
        run.side_effect = hermes_sequence(rejected_process(), rejected_process())

        outcome, _ = self.turn()

        self.assertEqual(outcome.error_code, "hermes_failed")
        self.assertEqual(run.call_count, 2)

    def test_a_rejected_source_counts_as_an_unsupported_option(self):
        error = "Gateway does not support source 'kore-health'"
        self.assertEqual(bridge.unsupported_hermes_options({"error": error}, f"Error: {error}\n"),
                         frozenset({"--source"}))

    @patch("bridge._run_process_bounded")
    def test_budget_then_source_rejections_end_in_an_answer(self, run):
        # Hermes v0.22.5 meldet erst --run-budget, nach dem Weglassen dann die Quelle: zwei Anpassungen, eine Antwort.
        run.side_effect = hermes_sequence(
            rejected_process(),
            rejected_process("Gateway does not support source 'kore-general'"),
            hermes_process(reply_stream("Da bin ich")),
        )

        outcome, journal = self.turn()

        self.assertIsNone(outcome.error_code)
        self.assertEqual(outcome.reply, "Da bin ich")
        last = run.call_args_list[-1].args[0]
        self.assertNotIn("--run-budget", last)
        self.assertNotIn("--source", last)
        self.assertIn("-t", last)
        self.assertIn("kore-hermes-option-dropped option=--source", journal)

    def test_gateway_session_ids_are_hermes_session_ids(self):
        gateway_id = "local-" + "6b6cd1f6" * 8
        self.assertIsNotNone(bridge._HERMES_SESSION_ID_RE.fullmatch(gateway_id))
        self.assertIsNotNone(bridge._HERMES_SESSION_ID_RE.fullmatch("20260924_120000_aaaaaa"))
        self.assertIsNone(bridge._HERMES_SESSION_ID_RE.fullmatch("local-../etc"))
        self.assertTrue(bridge.valid_session_token(gateway_id))
        with patch.object(bridge, "TOKEN", "t" * 48):
            ref = bridge.sign_session("health", stream_tests.CONVERSATION, gateway_id)
            self.assertLessEqual(len(ref.partition(":")[2]), 120)
            self.assertEqual(bridge.verify_session(ref, "health", stream_tests.CONVERSATION), gateway_id)

    def test_working_dirs_live_where_the_gateway_can_see_them(self):
        # Hermes v0.22.5 meldet den Arbeitsordner an das Gateway; ein Ordner im privaten /tmp der Bridge-Sandbox
        # existiert dort nicht (invalid_params). Der Ordner liegt unter dem Hermes-Datenordner, frisch und leer.
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as home, \
                patch.object(bridge, "HOME", home), patch.object(bridge, "HERMES_DATA_DIR", ""), \
                patch.dict(os.environ, {"KORE_HERMES_WORK_DIR": ""}):
            base = os.path.join(home, ".hermes", "kore-bridge-work")
            self.assertEqual(bridge.hermes_work_base(), base)
            with bridge.hermes_working_dir() as first:
                self.assertEqual(first, os.path.join(base, "chat"))
                self.assertEqual(os.listdir(first), [])
                self.assertEqual(os.stat(base).st_mode & 0o777, 0o700)
            self.assertTrue(os.path.isdir(first), "Der Ordner bleibt: Fortsetzen braucht ihn.")
        with patch.dict(os.environ, {"KORE_HERMES_WORK_DIR": "/srv/kore-work"}):
            self.assertEqual(bridge.hermes_work_base(), "/srv/kore-work")

    def test_only_comfort_options_are_droppable(self):
        self.assertEqual(bridge.DROPPABLE_HERMES_OPTIONS, frozenset({"--run-budget", "--max-turns", "--source"}))


if __name__ == "__main__":
    unittest.main()
