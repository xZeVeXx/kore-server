"""0.10 Task 24c: Journalzeile bei gescheitertem Turn und Hermes-Session-Titel im Turn-Ergebnis.

Die Journalzeile traegt nur Kennungen (Zeit, turn_id, Scope, error_code, Klasse, Exit-Code) und nie
Prompt, Antwort, Token, Health-Werte oder stderr-Rohtext. Der Titel kommt lesend aus
`sessions export`, ist bereinigt und hoechstens 60 Zeichen lang und fehlt, wenn Hermes keinen hat.
"""
import contextlib
import io
import json
import re
import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import bridge
from test_runner_stream_json import (
    CONVERSATION,
    TEST_TOKEN,
    hermes_process,
    reply_stream,
    result_record,
    stream_lines,
)

# Die echte Titelsuche; conftest.py schaltet sie fuer alle anderen Tests ab.
REAL_LOOKUP = bridge.lookup_session_title
SESSION_ID = "20260924_120000_aaaaaa"
JOURNAL_LINE = re.compile(
    r"kore-turn-failed time=\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z turn_id=(\S+) scope=(\S+) "
    r"error_code=(\S+) class=(\S+) exit=(\S+)\Z"
)


def journal_lines(text: str) -> list:
    return [JOURNAL_LINE.fullmatch(line).groups() for line in text.splitlines()
            if line.startswith("kore-turn-failed")]


class RunnerPatches(unittest.TestCase):
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


class FailureClassificationTests(RunnerPatches):
    def outcome(self, stdout, stderr="", returncode=1, session_ref=None):
        with patch("bridge._run_process_bounded", side_effect=hermes_process(stdout, stderr, returncode)):
            return bridge.run_turn("Hallo", scope="general", conversation_id=CONVERSATION,
                                   session_ref=session_ref, context=None, origin_ticket=None)

    def test_agent_init_failure_is_hermes_init_failed_with_the_exit_code(self):
        outcome = self.outcome(stream_lines(result_record(
            "", SESSION_ID, exit_code=1, error="credentials or agent init failed")))

        self.assertEqual(outcome.error_code, "hermes_failed")
        self.assertEqual(outcome.failure_class, "hermes_init_failed")
        self.assertEqual(outcome.exit_code, 1)

    def test_other_failures_are_classified_without_reading_their_text(self):
        cases = [
            ("kein result-Record", stream_lines({"type": "text", "text": "Teil"}), "", 1, "no_result"),
            ("anderer Fehler", stream_lines(result_record("", SESSION_ID, exit_code=2, error="boom")),
             "", 2, "hermes_error"),
            ("Exit != 0 trotz Erfolg", reply_stream("OK", SESSION_ID), "", 3, "hermes_exit_nonzero"),
            ("unbekannte Session", stream_lines(result_record(
                "", SESSION_ID, exit_code=1, error="credentials or agent init failed")),
             "Session not found: 20260101_000000_abcdef\n", 1, "session_unknown"),
        ]
        for name, stdout, stderr, returncode, expected in cases:
            with self.subTest(name):
                outcome = self.outcome(stdout, stderr, returncode)
                self.assertEqual(outcome.failure_class, expected)
                self.assertEqual(outcome.exit_code, returncode)

    def test_runner_limits_keep_their_own_class_and_have_no_exit_code(self):
        for error, expected in (
            (subprocess.TimeoutExpired(["hermes"], 1), "timeout"),
            (bridge.HermesOutputLimitExceeded("too much"), "output_limit"),
            (bridge.HermesInvocationError("group"), "process_error"),
        ):
            with self.subTest(expected), patch("bridge._run_process_bounded", side_effect=error):
                outcome = bridge.run_turn("Hallo", scope="general", conversation_id=CONVERSATION,
                                          session_ref=None, context=None, origin_ticket=None)
                self.assertEqual(outcome.failure_class, expected)
                self.assertIsNone(outcome.exit_code)

    def test_a_successful_turn_has_no_failure_class(self):
        outcome = self.outcome(reply_stream("OK", SESSION_ID), returncode=0)
        self.assertIsNone(outcome.error_code)
        self.assertIsNone(outcome.failure_class)


class JournalLineTests(unittest.TestCase):
    def test_the_line_names_only_identifiers(self):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            bridge.journal_turn_failure(turn_id="123e4567-e89b-42d3-a456-426614174000", scope="health",
                                        error_code="hermes_failed", failure_class="hermes_init_failed",
                                        exit_code=1)

        self.assertEqual(journal_lines(stderr.getvalue()), [(
            "123e4567-e89b-42d3-a456-426614174000", "health", "hermes_failed",
            "hermes_init_failed", "1",
        )])

    def test_missing_values_are_dashes_and_unexpected_values_never_pass_through(self):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            bridge.journal_turn_failure(turn_id=None, scope="general\nFAKE", error_code="timeout")

        self.assertEqual(len(stderr.getvalue().splitlines()), 1)
        self.assertEqual(journal_lines(stderr.getvalue()), [("-", "invalid", "timeout", "timeout", "-")])


class SessionTitleTests(RunnerPatches):
    def test_cleaning_removes_control_characters_and_collapses_whitespace(self):
        self.assertEqual(bridge.clean_session_title("  Plan\nfür\t\tMontag\x07\u200b "), "Plan für Montag")
        self.assertEqual(bridge.clean_session_title("\u202eAbc\u202c"), "Abc")
        for empty in ("", "   ", "\x00\n", None, 7, ["Titel"]):
            self.assertIsNone(bridge.clean_session_title(empty))

    def test_titles_are_cut_to_sixty_characters(self):
        cleaned = bridge.clean_session_title("Wort " * 30)
        self.assertEqual(len(cleaned), 60)
        self.assertTrue(cleaned.endswith("…"))
        exact = "x" * 60
        self.assertEqual(bridge.clean_session_title(exact), exact)

    def test_a_title_carrying_the_origin_ticket_is_dropped(self):
        self.assertIsNone(bridge.clean_session_title("Plan tkt.sig", origin_ticket="tkt.sig"))
        self.assertEqual(bridge.clean_session_title("Plan", origin_ticket="tkt.sig"), "Plan")

    def export(self, record, returncode=0):
        stdout = json.dumps(record, ensure_ascii=False) + "\n"
        return SimpleNamespace(args=[], stdout=stdout, stderr="", returncode=returncode)

    def test_lookup_reads_the_export_header_within_five_seconds(self):
        record = {"id": SESSION_ID, "source": "kore-general", "title": "Schlaf und Training",
                  "messages": [{"role": "user", "content": "geheim"}]}
        with patch("bridge._run_process_bounded", return_value=self.export(record)) as run:
            title = REAL_LOOKUP(SESSION_ID)

        self.assertEqual(title, "Schlaf und Training")
        args, kwargs = run.call_args
        self.assertEqual(args[0], ["/opt/hermes/bin/hermes", "-p", "kore-coach", "sessions", "export",
                                   "-", "--session-id", SESSION_ID])
        self.assertEqual(kwargs["timeout"], bridge.SESSION_TITLE_TIMEOUT_S)
        self.assertLessEqual(bridge.SESSION_TITLE_TIMEOUT_S, 5)

    def test_lookup_without_title_or_readable_session_is_none(self):
        cases = [
            ("kein Titel", {"return_value": self.export({"id": SESSION_ID, "title": None})}),
            ("andere Session", {"return_value": self.export({"id": "20260924_120000_bbbbbb", "title": "X"})}),
            ("Exit 1", {"return_value": self.export({"id": SESSION_ID, "title": "X"}, returncode=1)}),
            ("zu langsam", {"side_effect": subprocess.TimeoutExpired(["hermes"], 5)}),
        ]
        for name, behaviour in cases:
            with self.subTest(name), patch("bridge._run_process_bounded", **behaviour):
                self.assertIsNone(REAL_LOOKUP(SESSION_ID))

    def test_lookup_never_runs_for_an_id_outside_the_hermes_form(self):
        with patch("bridge._run_process_bounded") as run:
            self.assertIsNone(REAL_LOOKUP("--help"))
        run.assert_not_called()

    def run_chat(self, stdout, returncode=0, conversation_id=CONVERSATION):
        with patch("bridge._run_process_bounded", side_effect=hermes_process(stdout, "", returncode)):
            return bridge.run_turn("Hallo", scope="general", conversation_id=conversation_id,
                                   session_ref=None, context=None, origin_ticket=None)

    def test_a_successful_chat_turn_carries_the_cleaned_title(self):
        with patch.object(bridge, "lookup_session_title", return_value="Schlaf\n und Training") as lookup:
            outcome = self.run_chat(reply_stream("OK", SESSION_ID))

        lookup.assert_called_once_with(SESSION_ID)
        self.assertEqual(outcome.title, "Schlaf und Training")
        self.assertEqual(bridge.turn_outcome_payload(outcome)["title"], "Schlaf und Training")

    def test_without_a_title_the_payload_has_no_title_key(self):
        outcome = self.run_chat(reply_stream("OK", SESSION_ID))
        self.assertIsNone(outcome.title)
        self.assertNotIn("title", bridge.turn_outcome_payload(outcome))

    def test_a_failing_lookup_never_fails_the_turn(self):
        with patch.object(bridge, "lookup_session_title", side_effect=RuntimeError("kaputt")):
            outcome = self.run_chat(reply_stream("OK", SESSION_ID))
        self.assertIsNone(outcome.error_code)
        self.assertIsNone(outcome.title)

    def test_no_lookup_for_failed_turns_or_the_day_adjustment(self):
        with patch.object(bridge, "lookup_session_title", return_value="X") as lookup:
            failed = self.run_chat(stream_lines(result_record("", SESSION_ID, exit_code=1)), returncode=1)
            adjusted = self.run_chat(reply_stream("{}", SESSION_ID), conversation_id=None)
        lookup.assert_not_called()
        self.assertIsNone(failed.title)
        self.assertIsNone(adjusted.title)

    def test_titles_are_optional_and_not_part_of_the_app_contract(self):
        from test_contract import contract_values

        self.assertIn("turn_titles_v1", bridge.capabilities_payload()["capabilities"])
        self.assertNotIn("turn_titles_v1", contract_values())


if __name__ == "__main__":
    unittest.main()
