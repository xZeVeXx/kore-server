"""0.14.2: Sitzungsbuch der Bridge und gelernte Hermes-Optionen ueber einen Neustart.

Hermes v0.22.5 startet Chats ueber sein Gateway; das nimmt nur feste Quellen an (`oneshot` …), IDs heissen
`local-<hex>`. Die Bridge findet ihre neuen Sessions deshalb nicht mehr ueber `--source kore-<scope>`, sondern ueber
ihr eigenes Buch. Hermes ist wie in test_forget.py durch den Nachbau der `sessions`-CLI ersetzt.
"""
import io
import json
import os
import stat
import unittest
from contextlib import redirect_stderr
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import bridge
import test_runner_stream_json as stream_tests
from test_forget import ForgetTestCase, session
from test_hermes_options import rejected_process
from test_runner_stream_json import CONVERSATION, hermes_process, hermes_sequence, reply_stream

GATEWAY_A = "local-" + "a1" * 16
GATEWAY_B = "local-" + "b2" * 16
GATEWAY_C = "local-" + "c3" * 16
GATEWAY_D = "local-" + "d4" * 16
LEGACY = "20260901_070000_000001"
NOW = datetime.now(timezone.utc)
# Vor den conftest-Fixtures gelesen: die echte Funktion, nicht der Ersatz.
REAL_HERMES_VERSION = bridge.hermes_version


def ledger_file() -> dict:
    with open(bridge.SESSION_LEDGER.path, encoding="utf-8") as handle:
        return json.load(handle)


def fresh_ledger():
    """Ein neuer Prozess: liest das Buch von der Platte statt aus dem Speicher."""
    return bridge.SessionLedger(bridge.BRIDGE_STATE_DIR)


class LedgerAfterTurnTests(unittest.TestCase):
    def setUp(self):
        stream_tests.StreamJsonRunnerTests.setUp(self)

    def turn(self, *, scope="general", conversation_id=CONVERSATION, session_ref=None):
        return bridge.run_turn("Hallo", scope=scope, conversation_id=conversation_id, session_ref=session_ref,
                               context=None, origin_ticket=None)

    @patch("bridge._run_process_bounded")
    def test_a_successful_chat_turn_writes_the_session_into_the_ledger(self, run):
        run.side_effect = hermes_sequence(hermes_process(reply_stream(session_id=GATEWAY_A)))

        outcome = self.turn()

        self.assertIsNone(outcome.error_code)
        rows = ledger_file()["sessions"]
        self.assertEqual(list(rows), [GATEWAY_A])
        self.assertEqual(rows[GATEWAY_A]["scope"], "general")
        used = bridge._parse_iso8601(rows[GATEWAY_A]["last_used"])
        self.assertLess(abs(used - datetime.now(timezone.utc)), timedelta(minutes=1))
        self.assertEqual(set(rows[GATEWAY_A]), {"scope", "last_used"}, "Keine Inhalte, keine Chat-IDs.")
        self.assertEqual(stat.S_IMODE(os.stat(bridge.SESSION_LEDGER.path).st_mode), 0o600)
        self.assertEqual([name for name in os.listdir(bridge.BRIDGE_STATE_DIR) if name.endswith(".tmp")], [])

    @patch("bridge._run_process_bounded")
    def test_the_daily_adjustment_without_a_chat_is_recorded_too(self, run):
        run.side_effect = hermes_sequence(hermes_process(reply_stream(session_id=GATEWAY_B)))

        self.turn(scope="health", conversation_id=None)

        self.assertEqual(fresh_ledger().session_ids("health"), [GATEWAY_B])

    @patch("bridge._run_process_bounded")
    def test_a_resumed_session_moves_its_last_use_forward(self, run):
        bridge.SESSION_LEDGER.touch(GATEWAY_A, "general", now=NOW - timedelta(days=20))
        run.side_effect = hermes_sequence(hermes_process(reply_stream(session_id=GATEWAY_A)))

        self.turn(session_ref=bridge.sign_session("general", CONVERSATION, GATEWAY_A))

        cutoff = datetime.now(timezone.utc) - timedelta(days=1)
        self.assertEqual(fresh_ledger().session_ids("general"), [GATEWAY_A])
        self.assertEqual(fresh_ledger().session_ids("general", used_before=cutoff), [])

    @patch("bridge._run_process_bounded")
    def test_a_failed_turn_writes_nothing(self, run):
        run.side_effect = hermes_sequence(hermes_process(reply_stream(session_id=GATEWAY_A), returncode=1))

        outcome = self.turn()

        self.assertEqual(outcome.error_code, "hermes_failed")
        self.assertFalse(os.path.exists(bridge.SESSION_LEDGER.path))

    @patch("bridge._run_process_bounded")
    def test_a_session_hermes_no_longer_knows_leaves_the_ledger(self, run):
        bridge.SESSION_LEDGER.touch(GATEWAY_A, "general")
        run.side_effect = hermes_sequence(
            hermes_process("", stderr=f"Session not found: {GATEWAY_A}\n", returncode=1),
            hermes_process(reply_stream(session_id=GATEWAY_B)),
        )

        outcome = self.turn(session_ref=bridge.sign_session("general", CONVERSATION, GATEWAY_A))

        self.assertEqual(outcome.reset_reason, "expired")
        self.assertEqual(fresh_ledger().session_ids("general"), [GATEWAY_B])

    @patch("bridge._run_process_bounded")
    def test_an_unwritable_state_dir_never_costs_the_answer(self, run):
        os.makedirs(bridge.BRIDGE_STATE_DIR)
        os.chmod(bridge.BRIDGE_STATE_DIR, 0o500)
        self.addCleanup(os.chmod, bridge.BRIDGE_STATE_DIR, 0o700)
        run.side_effect = hermes_sequence(hermes_process(reply_stream("Da bin ich", session_id=GATEWAY_A)))

        with redirect_stderr(io.StringIO()) as journal:
            outcome = self.turn()

        self.assertIsNone(outcome.error_code)
        self.assertEqual(outcome.reply, "Da bin ich")
        self.assertIn("kore-session-ledger-write-failed error=PermissionError", journal.getvalue())


class LedgerFileTests(unittest.TestCase):
    def test_a_broken_file_starts_an_empty_ledger_with_a_journal_line(self):
        os.makedirs(bridge.BRIDGE_STATE_DIR)
        for content, reason in ((b"{kaputt", "malformed"), (b"[]", "malformed"),
                                (b'{"version": 9, "sessions": {}}', "malformed"), (b"\xff\xfe", "malformed")):
            with self.subTest(content=content):
                with open(bridge.SESSION_LEDGER.path, "wb") as handle:
                    handle.write(content)
                ledger = fresh_ledger()
                with redirect_stderr(io.StringIO()) as journal:
                    self.assertEqual(ledger.session_ids("general"), [])
                self.assertIn(f"kore-session-ledger-reset reason={reason}", journal.getvalue())
                ledger.touch(GATEWAY_A, "health")
                self.assertEqual(fresh_ledger().session_ids("health"), [GATEWAY_A])

    def test_invalid_entries_are_skipped_and_the_rest_stays(self):
        os.makedirs(bridge.BRIDGE_STATE_DIR)
        with open(bridge.SESSION_LEDGER.path, "w", encoding="utf-8") as handle:
            json.dump({"version": 1, "sessions": {
                GATEWAY_A: {"scope": "health", "last_used": "2026-10-30T08:00:00Z"},
                GATEWAY_B: {"scope": "telegram", "last_used": "2026-10-30T08:00:00Z"},
                "-rf": {"scope": "general", "last_used": "2026-10-30T08:00:00Z"},
                GATEWAY_C: {"scope": "general", "last_used": "gestern"},
            }}, handle)

        with redirect_stderr(io.StringIO()) as journal:
            self.assertEqual(fresh_ledger().session_ids("health"), [GATEWAY_A])
        self.assertIn("kore-session-ledger-skipped count=3", journal.getvalue())

    def test_the_oldest_entries_fall_out_above_the_limit(self):
        ledger = bridge.SessionLedger(bridge.BRIDGE_STATE_DIR, max_entries=3)
        for days, sid in ((4, GATEWAY_A), (3, GATEWAY_B), (2, GATEWAY_C)):
            ledger.touch(sid, "general", now=NOW - timedelta(days=days))

        with redirect_stderr(io.StringIO()) as journal:
            ledger.touch(GATEWAY_D, "health", now=NOW)

        self.assertIn("kore-session-ledger-full dropped=1", journal.getvalue())
        self.assertEqual(fresh_ledger().session_ids("general"), [GATEWAY_C, GATEWAY_B])
        self.assertEqual(fresh_ledger().session_ids("health"), [GATEWAY_D])

    def test_writes_replace_the_file_atomically(self):
        bridge.SESSION_LEDGER.touch(GATEWAY_A, "general")
        replaced = []
        real_replace = os.replace

        def spy(src, dst):
            replaced.append((os.path.basename(src), os.path.basename(dst),
                             stat.S_IMODE(os.stat(src).st_mode)))
            return real_replace(src, dst)

        with patch("bridge.os.replace", side_effect=spy):
            bridge.SESSION_LEDGER.remove([GATEWAY_A])

        self.assertEqual(len(replaced), 1)
        tmp, target, mode = replaced[0]
        self.assertTrue(tmp.startswith(".bridge-sessions.json.") and tmp.endswith(".tmp"))
        self.assertEqual((target, mode), ("bridge-sessions.json", 0o600))
        self.assertEqual(ledger_file(), {"version": 1, "sessions": {}})

    def test_a_failed_write_leaves_the_old_file_and_no_temp_file(self):
        bridge.SESSION_LEDGER.touch(GATEWAY_A, "general")
        with patch("bridge.os.replace", side_effect=OSError("disk full")), redirect_stderr(io.StringIO()) as journal:
            bridge.SESSION_LEDGER.touch(GATEWAY_B, "general")

        self.assertIn("kore-session-ledger-write-failed error=OSError", journal.getvalue())
        self.assertEqual(list(ledger_file()["sessions"]), [GATEWAY_A])
        self.assertEqual([name for name in os.listdir(bridge.BRIDGE_STATE_DIR) if name.endswith(".tmp")], [])


class LedgerForgetTests(ForgetTestCase):
    def test_scope_forget_deletes_ledger_and_legacy_sessions_and_nothing_foreign(self):
        cli = self.use_cli({
            GATEWAY_A: session("oneshot", end_reason="compression"),
            GATEWAY_B: session("oneshot", parent=GATEWAY_A),
            GATEWAY_C: session("oneshot"),  # gehoert dem allgemeinen Scope
            GATEWAY_D: session("oneshot"),  # ein eigener `hermes -z`-Lauf, nie im Buch
            LEGACY: session("kore-health"),  # Altbestand von vor dem Hermes-Update
            "20260905_070000_000005": session("telegram"),
            "20260906_070000_000006": session("cli", title="kore-health Notizen"),
        })
        bridge.SESSION_LEDGER.touch(GATEWAY_B, "health")
        bridge.SESSION_LEDGER.touch(GATEWAY_C, "general")

        result = bridge.forget(scope="health", store=self.store)

        self.assertEqual(result, {"deleted": 3, "not_found": 0, "failed": 0})
        self.assertEqual(cli.deleted_ids(), [GATEWAY_B, GATEWAY_A, LEGACY])
        self.assertEqual(sorted(cli.sessions), sorted([GATEWAY_C, GATEWAY_D, "20260905_070000_000005",
                                                       "20260906_070000_000006"]))
        self.assertEqual(fresh_ledger().session_ids("health"), [])
        self.assertEqual(fresh_ledger().session_ids("general"), [GATEWAY_C])

    def test_forget_all_covers_both_scopes_of_the_ledger(self):
        cli = self.use_cli({GATEWAY_A: session("oneshot"), GATEWAY_B: session("oneshot"),
                            GATEWAY_C: session("tui")})
        bridge.SESSION_LEDGER.touch(GATEWAY_A, "general")
        bridge.SESSION_LEDGER.touch(GATEWAY_B, "health")

        result = bridge.forget(scope="all", store=self.store)

        self.assertEqual(result, {"deleted": 2, "not_found": 0, "failed": 0})
        self.assertEqual(list(cli.sessions), [GATEWAY_C])
        self.assertFalse(fresh_ledger().session_ids("general") + fresh_ledger().session_ids("health"))

    def test_a_ledger_chain_stops_at_a_foreign_or_non_compression_parent(self):
        cli = self.use_cli({
            GATEWAY_A: session("telegram", end_reason="compression"),
            GATEWAY_B: session("oneshot", parent=GATEWAY_A),
            GATEWAY_C: session("oneshot", end_reason="cli_exit"),
            GATEWAY_D: session("oneshot", parent=GATEWAY_C),
        })
        bridge.SESSION_LEDGER.touch(GATEWAY_B, "health")
        bridge.SESSION_LEDGER.touch(GATEWAY_D, "health")

        result = bridge.forget(scope="health", store=self.store)

        self.assertEqual(result, {"deleted": 2, "not_found": 0, "failed": 0})
        self.assertEqual(sorted(cli.sessions), [GATEWAY_A, GATEWAY_C])

    def test_a_ledger_session_hermes_already_lost_is_not_found_and_leaves_the_ledger(self):
        cli = self.use_cli({})
        bridge.SESSION_LEDGER.touch(GATEWAY_A, "health")

        result = bridge.forget(scope="health", store=self.store)

        self.assertEqual(result, {"deleted": 0, "not_found": 1, "failed": 0})
        self.assertEqual(cli.deleted_ids(), [])
        self.assertEqual(fresh_ledger().session_ids("health"), [])

    def test_an_undeletable_ledger_session_is_not_confirmed_and_stays_in_the_ledger(self):
        cli = self.use_cli({GATEWAY_A: session("oneshot")})
        cli.fail_actions.add("delete")
        bridge.SESSION_LEDGER.touch(GATEWAY_A, "health")

        result = bridge.forget(scope="health", store=self.store)

        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["deleted"], 0)
        self.assertEqual(fresh_ledger().session_ids("health"), [GATEWAY_A])

    def test_forgetting_one_chat_also_takes_it_out_of_the_ledger(self):
        self.use_cli({GATEWAY_A: session("oneshot"), GATEWAY_B: session("oneshot")})
        bridge.SESSION_LEDGER.touch(GATEWAY_A, "general")
        bridge.SESSION_LEDGER.touch(GATEWAY_B, "general")

        result = bridge.forget(conversation_id=CONVERSATION,
                               sessions=[bridge.sign_session("general", CONVERSATION, GATEWAY_A)],
                               store=self.store)

        self.assertEqual(result, {"deleted": 1, "not_found": 0, "failed": 0})
        self.assertEqual(fresh_ledger().session_ids("general"), [GATEWAY_B])

    def test_the_time_budget_reports_unvisited_ledger_sessions_as_failed(self):
        cli = self.use_cli({GATEWAY_A: session("oneshot"), GATEWAY_B: session("oneshot")})
        bridge.SESSION_LEDGER.touch(GATEWAY_A, "health")
        bridge.SESSION_LEDGER.touch(GATEWAY_B, "health")

        with patch.object(bridge, "FORGET_BUDGET_S", -1):
            result = bridge.forget(scope="health", store=self.store)

        self.assertEqual(result, {"deleted": 0, "not_found": 0, "failed": 3})  # zwei aus dem Buch, ein Altbestand
        self.assertEqual(cli.deleted_ids(), [])


class LedgerRetentionTests(ForgetTestCase):
    def test_only_expired_ledger_sessions_go_with_their_compression_predecessors(self):
        cli = self.use_cli({
            GATEWAY_A: session("oneshot", end_reason="compression"),
            GATEWAY_B: session("oneshot", parent=GATEWAY_A),
            GATEWAY_C: session("oneshot"),
            GATEWAY_D: session("oneshot", idle_days=90),  # nie im Buch: kein Kore-Beleg
            LEGACY: session("kore-general", idle_days=31),
            "20260920_070000_000003": session("kore-health", idle_days=4),
        })
        bridge.SESSION_LEDGER.touch(GATEWAY_B, "health", now=NOW - timedelta(days=31))
        bridge.SESSION_LEDGER.touch(GATEWAY_C, "general", now=NOW - timedelta(days=29))

        result = bridge.prune_expired(self.store)

        self.assertEqual(result, {"deleted": 3, "not_found": 0, "failed": 0})
        self.assertEqual(sorted(cli.deleted_ids()), sorted([GATEWAY_B, GATEWAY_A, LEGACY]))
        self.assertEqual(sorted(cli.sessions), sorted([GATEWAY_C, GATEWAY_D, "20260920_070000_000003"]))
        self.assertEqual(fresh_ledger().session_ids("general"), [GATEWAY_C])
        self.assertEqual(fresh_ledger().session_ids("health"), [])

    def test_the_retention_days_come_from_the_configuration(self):
        cli = self.use_cli({GATEWAY_A: session("oneshot")})
        bridge.SESSION_LEDGER.touch(GATEWAY_A, "general", now=NOW - timedelta(days=8))

        with patch.object(bridge, "SESSION_RETENTION_DAYS", 7):
            result = bridge.prune_expired(self.store)

        self.assertEqual(result["deleted"], 1)
        self.assertEqual(cli.sessions, {})

    def test_a_turn_starting_in_the_middle_stops_the_run_but_keeps_the_ledger_true(self):
        cli = self.use_cli({GATEWAY_A: session("oneshot"), GATEWAY_B: session("oneshot")})
        bridge.SESSION_LEDGER.touch(GATEWAY_A, "general", now=NOW - timedelta(days=40))
        bridge.SESSION_LEDGER.touch(GATEWAY_B, "general", now=NOW - timedelta(days=41))
        original = cli._delete

        def delete_then_start_a_turn(rest):
            outcome = original(rest)
            if not self.store.active_count():
                self.running_turn()
            return outcome

        cli._delete = delete_then_start_a_turn

        self.assertIsNone(bridge.prune_expired(self.store))
        self.assertEqual(cli.deleted_ids(), [GATEWAY_A])
        self.assertEqual(fresh_ledger().session_ids("general"), [GATEWAY_B])


class LearnedHermesOptionsTests(unittest.TestCase):
    VERSION = "Hermes Agent v0.22.5 (2026.10.28)"

    def setUp(self):
        stream_tests.StreamJsonRunnerTests.setUp(self)
        bridge._dropped_hermes_options.clear()
        self.addCleanup(bridge._dropped_hermes_options.clear)
        self.version = self.VERSION
        patcher = patch.object(bridge, "hermes_version", side_effect=lambda: self.version)
        patcher.start()
        self.addCleanup(patcher.stop)

    def options_path(self):
        return os.path.join(bridge.BRIDGE_STATE_DIR, bridge.HERMES_OPTIONS_FILE)

    @patch("bridge._run_process_bounded")
    def learn_by_turn(self, run):
        run.side_effect = hermes_sequence(
            rejected_process(),
            rejected_process("Gateway does not support source 'kore-general'"),
            hermes_process(reply_stream()),
        )
        with redirect_stderr(io.StringIO()):
            outcome = bridge.run_turn("Hallo", scope="general", conversation_id=CONVERSATION, session_ref=None,
                                      context=None, origin_ticket=None)
        self.assertIsNone(outcome.error_code)

    def restart(self):
        bridge._dropped_hermes_options.clear()
        with redirect_stderr(io.StringIO()) as journal:
            bridge.restore_learned_hermes_options()
        return journal.getvalue()

    def test_learned_drops_are_stored_with_the_hermes_version(self):
        self.learn_by_turn()

        with open(self.options_path(), encoding="utf-8") as handle:
            stored = json.load(handle)
        self.assertEqual(stored, {"hermes_version": self.VERSION, "dropped": ["--run-budget", "--source"]})
        self.assertEqual(stat.S_IMODE(os.stat(self.options_path()).st_mode), 0o600)

    @patch("bridge._run_process_bounded")
    def test_after_a_restart_with_the_same_hermes_the_first_turn_needs_no_retry(self, run):
        self.learn_by_turn()
        journal = self.restart()
        run.side_effect = hermes_sequence(hermes_process(reply_stream()))

        outcome = bridge.run_turn("Hallo", scope="general", conversation_id=CONVERSATION, session_ref=None,
                                  context=None, origin_ticket=None)

        self.assertIsNone(outcome.error_code)
        self.assertEqual(run.call_count, 1)
        args = run.call_args.args[0]
        self.assertNotIn("--run-budget", args)
        self.assertNotIn("--source", args)
        self.assertIn("kore-hermes-option-restored option=--run-budget", journal)
        self.assertIn("kore-hermes-option-restored option=--source", journal)

    def test_a_new_hermes_version_relearns(self):
        self.learn_by_turn()
        self.version = "Hermes Agent v0.23.0 (2026.11.20)"

        journal = self.restart()

        self.assertEqual(bridge._dropped_hermes_options, set())
        self.assertIn("kore-hermes-options-reset reason=hermes_version_changed", journal)

    def test_an_unreadable_version_relearns(self):
        self.learn_by_turn()
        self.version = None

        journal = self.restart()

        self.assertEqual(bridge._dropped_hermes_options, set())
        self.assertIn("kore-hermes-options-reset reason=hermes_version_unknown", journal)

    def test_without_a_readable_version_nothing_is_stored(self):
        self.version = None
        self.learn_by_turn()
        self.assertFalse(os.path.exists(self.options_path()))

    def test_a_stored_safety_option_is_never_restored(self):
        os.makedirs(bridge.BRIDGE_STATE_DIR)
        with open(self.options_path(), "w", encoding="utf-8") as handle:
            json.dump({"hermes_version": self.VERSION, "dropped": ["-t", "--query-file", "--max-turns", 7]}, handle)

        self.restart()

        self.assertEqual(bridge._dropped_hermes_options, {"--max-turns"})

    def test_a_broken_file_is_ignored_with_a_journal_line(self):
        os.makedirs(bridge.BRIDGE_STATE_DIR)
        for content in (b"{kaputt", b'{"hermes_version": 1, "dropped": []}', b'{"hermes_version": "x"}'):
            with self.subTest(content=content):
                with open(self.options_path(), "wb") as handle:
                    handle.write(content)
                journal = self.restart()
                self.assertEqual(bridge._dropped_hermes_options, set())
                self.assertIn("kore-hermes-options-reset reason=malformed", journal)

    def test_a_missing_file_is_silent(self):
        self.assertEqual(self.restart(), "")
        self.assertEqual(bridge._dropped_hermes_options, set())


class HermesVersionTests(unittest.TestCase):
    """Die echte `hermes_version` (conftest ersetzt sie sonst): erste Zeile von `hermes --version`, sonst None."""

    def setUp(self):
        stream_tests.StreamJsonRunnerTests.setUp(self)

    @patch("bridge._run_process_bounded")
    def test_the_first_non_empty_line_is_the_version(self, run):
        run.return_value = SimpleNamespace(stdout="\nHermes Agent v0.22.5 (2026.10.28)\nProject: …\n", returncode=0)

        self.assertEqual(REAL_HERMES_VERSION(), "Hermes Agent v0.22.5 (2026.10.28)")
        self.assertEqual(run.call_args.args[0], ["/opt/hermes/bin/hermes", "--version"])

    @patch("bridge._run_process_bounded")
    def test_an_unreadable_version_is_none(self, run):
        for outcome in (SimpleNamespace(stdout="Hermes\n", returncode=1),
                        SimpleNamespace(stdout="\n", returncode=0),
                        SimpleNamespace(stdout="x" * 500 + "\n", returncode=0),
                        OSError("no hermes")):
            with self.subTest(outcome=outcome):
                run.side_effect = [outcome]
                self.assertIsNone(REAL_HERMES_VERSION())


if __name__ == "__main__":
    unittest.main()
