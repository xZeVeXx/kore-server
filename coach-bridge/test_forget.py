"""/coach/forget und 30-Tage-Verfall der Kore-Sessions (Hermes v0.21.4).

Hermes ist durch einen Nachbau der `hermes sessions`-CLI v0.21.4 ersetzt (Ausgabeformate aus
hermes_cli/sessions_cmd.py). Befunde, die hier festgeschrieben sind:
- `sessions prune` ohne `--dry-run` verweigert Hermes, solange Hermes Desktop die state.db des
  Profils haelt. Die Bridge waehlt deshalb nur per `--dry-run`/`list` aus und loescht jede ID
  einzeln mit `sessions delete --yes <id>`.
- `sessions delete` loescht keine Kompressions-Vorgaenger; die Bridge laeuft die Kette selbst ab.
"""
import http.client
import json
import subprocess
import threading
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import patch

import bridge
from test_runner_stream_json import CONVERSATION, OTHER_CONVERSATION, TEST_TOKEN

PROFILE = "kore-coach"
HERMES = "/opt/hermes/bin/hermes"
TIP = "20260924_120000_aaaaaa"
MIDDLE = "20260920_080000_bbbbbb"
ROOT = "20260901_070000_cccccc"


def session(source, *, parent=None, end_reason="cli_exit", ended=True, idle_days=1, title=None,
            preview="Wie war mein Schlaf diese Woche?", archived=False, pinned=False, hidden=False):
    return {"source": source, "parent": parent, "end_reason": end_reason, "ended": ended,
            "idle_days": idle_days, "title": title, "preview": preview,
            "archived": archived, "pinned": pinned, "hidden": hidden}


class FakeSessionsCLI:
    """Steht fuer _run_process_bounded bei `hermes -p <profil> sessions …`.

    `held=True` bildet Hermes Desktop nach: `prune` ohne `--dry-run` wird verweigert.
    """

    def __init__(self, sessions, held=True, log=None):
        self.sessions = {sid: dict(row) for sid, row in sessions.items()}
        self.held = held
        self.calls = []
        self.log = log if log is not None else []
        self.fail_actions = set()

    def __call__(self, args, **kwargs):
        args = list(args)
        self.calls.append(args)
        self.log.append(("cli", args[4:]))
        assert args[:4] == [HERMES, "-p", PROFILE, "sessions"], args
        action, rest = args[4], args[5:]
        if action in self.fail_actions:
            raise subprocess.TimeoutExpired(args, 60)
        handler = getattr(self, f"_{action}")
        stdout, returncode = handler(rest)
        return SimpleNamespace(args=args, stdout=stdout, stderr="", returncode=returncode)

    # -- Nachbau der Ausgaben ------------------------------------------------------------

    @staticmethod
    def _option(rest, name, default=None):
        return rest[rest.index(name) + 1] if name in rest else default

    def _list(self, rest):
        source = self._option(rest, "--source")
        limit = int(self._option(rest, "--limit", "20"))
        # Wie list_sessions_rich: archivierte und versteckte Sessions fehlen, ein Flag dafuer gibt
        # es bei `sessions list` nicht.
        rows = [
            (sid, row) for sid, row in self.sessions.items()
            if row["source"] == source and row["parent"] is None
            and not row["archived"] and not row["hidden"]
        ][:limit]
        if not rows:
            return "No sessions found.\n", 0
        lines = [f"{'Preview':<50} {'Last Active':<13} {'Src':<6} {'ID'}", "─" * 95]
        for sid, row in rows:
            lines.append(f"{row['preview']:<50} {'3d ago':<13} {row['source']:<6} {sid}")
        return "\n".join(lines) + "\n", 0

    def _prune(self, rest):
        if "--dry-run" not in rest and self.held:
            return ("Refusing `hermes sessions prune`: another process is using "
                    "/srv/coach/.hermes/profiles/kore-coach/state.db\n"), 1
        source = self._option(rest, "--source")
        older = self._option(rest, "--older-than")
        # Wie _cmd_prune_or_archive: archivierte und angeheftete Sessions nur mit den Flags,
        # offene nie (nur die Notiz zaehlt sie).
        include_archived = "--include-archived" in rest
        include_pinned = "--include-pinned" in rest

        def matches(row):
            return (row["source"] == source
                    and (older is None or row["idle_days"] > float(older))
                    and (include_archived or not row["archived"])
                    and (include_pinned or not row["pinned"]))

        candidates = [(sid, row) for sid, row in self.sessions.items() if matches(row) and row["ended"]]
        lines = []
        open_count = sum(1 for row in self.sessions.values() if matches(row) and not row["ended"])
        if open_count:
            lines.append(f"Note: {open_count} open session{'' if open_count == 1 else 's'} also match "
                         "these filters but will be skipped because prune only deletes ended "
                         "sessions. Use `hermes sessions delete <id>` to remove one explicitly.")
        if not candidates:
            lines.append(f"No sessions match (source={source}).")
            return "\n".join(lines) + "\n", 0
        lines.append(f"{len(candidates)} session(s) match (source={source}; oldest activity "
                     "2026-08-01 10:00, newest activity 2026-08-20 10:00):")
        for sid, row in candidates:
            lines.append(f"  {sid}  {'2026-08-01 10:00':<17} {row['source']:<10} "
                         f"{'example-model':<24} {3:>4} msgs  {(row['title'] or '')[:36]}")
        if "--dry-run" in rest:
            lines.append("Dry run — nothing deleted.")
            return "\n".join(lines) + "\n", 0
        raise AssertionError("the bridge must never prune for real")

    def _export(self, rest):
        assert rest[0] == "-", rest
        sid = self._option(rest, "--session-id")
        row = self.sessions.get(sid)
        if row is None:
            return f"No session '{sid}'. Run: hermes sessions list to find the id.\n", 0
        record = {
            "id": sid, "source": row["source"], "parent_session_id": row["parent"],
            "end_reason": row["end_reason"], "title": row["title"],
            "messages": [{"role": "user", "content": "[KORE-KONTEXT] Schlaf 6 h"}],
        }
        return json.dumps(record, ensure_ascii=False) + "\n", 0

    def _delete(self, rest):
        assert rest[0] == "--yes", rest
        sid = rest[1]
        if sid not in self.sessions:
            return f"No session '{sid}'. Run: hermes sessions list to find the id.\n", 1
        del self.sessions[sid]
        for row in self.sessions.values():
            if row["parent"] == sid:
                row["parent"] = None  # Hermes verwaist Kompressions-Kinder
        return f"Deleted session '{sid}'.\n", 0

    # -- Auswertung -----------------------------------------------------------------------

    def deleted_ids(self):
        return [call[6] for call in self.calls if call[4] == "delete"]

    def actions(self):
        return [call[4] for call in self.calls]


class ForgetTestCase(unittest.TestCase):
    def setUp(self):
        for name, value in (
            ("TOKEN", TEST_TOKEN),
            ("PROFILE", PROFILE),
            ("HERMES", HERMES),
            ("SESSION_RETENTION_DAYS", 30),
        ):
            patcher = patch.object(bridge, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.log = []
        self.store = bridge.TurnStore()
        self.terminated = []

        def terminate(process):
            self.terminated.append(process)
            self.log.append(("terminate", process.pid))
            return True

        patcher = patch("bridge._terminate_process_group", side_effect=terminate)
        patcher.start()
        self.addCleanup(patcher.stop)

    def use_cli(self, sessions, **kwargs):
        cli = FakeSessionsCLI(sessions, log=self.log, **kwargs)
        patcher = patch("bridge._run_process_bounded", side_effect=cli)
        patcher.start()
        self.addCleanup(patcher.stop)
        return cli

    def running_turn(self, conversation=CONVERSATION, scope="general", pid=41_000):
        turn, _ = self.store.create(conversation, str(uuid.uuid4()), scope)
        self.store.attach_process(turn, SimpleNamespace(pid=pid))
        return turn

    def finished_turn(self, conversation=CONVERSATION, scope="general"):
        turn, _ = self.store.create(conversation, str(uuid.uuid4()), scope)
        self.store.finish(turn, bridge.TurnOutcome("OK", False, None, "new", None, None))
        return turn


class ForgetBySessionTests(ForgetTestCase):
    def test_a_signed_reference_deletes_the_session_and_its_compression_chain(self):
        cli = self.use_cli({
            ROOT: session("kore-health", end_reason="compression", idle_days=20),
            MIDDLE: session("kore-health", parent=ROOT, end_reason="compression", idle_days=5),
            TIP: session("kore-health", parent=MIDDLE),
            "20260924_130000_dddddd": session("kore-health"),
        })
        ref = bridge.sign_session("health", CONVERSATION, TIP)

        result = bridge.forget(conversation_id=CONVERSATION, sessions=[ref], store=self.store)

        self.assertEqual(result, {"deleted": 3, "not_found": 0, "failed": 0})
        self.assertEqual(cli.deleted_ids(), [TIP, MIDDLE, ROOT])
        self.assertIn([HERMES, "-p", PROFILE, "sessions", "delete", "--yes", TIP], cli.calls)
        self.assertIn("20260924_130000_dddddd", cli.sessions)
        self.assertNotIn("prune", cli.actions())

    def test_a_line_separator_in_the_export_keeps_the_chain_readable(self):
        # Hermes exportiert mit ensure_ascii=False; U+2028/U+2029/U+0085 im Titel oder Verlauf
        # stehen roh in der Zeile. splitlines() zerlegte den Record: Kette unlesbar, nie bestaetigt.
        cli = self.use_cli({
            ROOT: session("kore-health", end_reason="compression", title="Schlaf\u2028Woche"),
            TIP: session("kore-health", parent=ROOT, title="Plan\u2029neu\x85"),
        })
        ref = bridge.sign_session("health", CONVERSATION, TIP)

        result = bridge.forget(conversation_id=CONVERSATION, sessions=[ref], store=self.store)

        self.assertEqual(result, {"deleted": 2, "not_found": 0, "failed": 0})
        self.assertEqual(cli.deleted_ids(), [TIP, ROOT])

    def test_signature_is_checked_before_anything_is_deleted(self):
        cli = self.use_cli({TIP: session("kore-general")})
        good = bridge.sign_session("general", CONVERSATION, TIP)
        foreign_chat = bridge.sign_session("general", OTHER_CONVERSATION, TIP)
        tampered = good[:-1] + ("A" if good[-1] != "A" else "B")
        foreign_scope = "health:" + good.partition(":")[2]
        unsigned = f"general:{TIP}"

        for ref in (foreign_chat, tampered, foreign_scope, unsigned):
            with self.subTest(ref=ref):
                result = bridge.forget(conversation_id=CONVERSATION, sessions=[ref], store=self.store)
                self.assertEqual(result, {"deleted": 0, "not_found": 0, "failed": 1})
        self.assertEqual(cli.calls, [])
        self.assertIn(TIP, cli.sessions)

    def test_an_already_gone_session_is_not_found(self):
        cli = self.use_cli({})
        ref = bridge.sign_session("general", CONVERSATION, TIP)

        result = bridge.forget(conversation_id=CONVERSATION, sessions=[ref], store=self.store)

        self.assertEqual(result, {"deleted": 0, "not_found": 1, "failed": 0})
        self.assertEqual(cli.deleted_ids(), [])

    def test_never_deletes_a_session_of_another_source(self):
        # Eine signierte ID gehoert immer zu Kore; die Quelle wird trotzdem geprueft, und die
        # Kette endet an jedem Glied, das nicht aus derselben Kore-Quelle stammt.
        cli = self.use_cli({
            ROOT: session("telegram", end_reason="compression"),
            TIP: session("kore-general", parent=ROOT),
            MIDDLE: session("cli"),
        })
        result = bridge.forget(
            conversation_id=CONVERSATION,
            sessions=[
                bridge.sign_session("general", CONVERSATION, TIP),
                bridge.sign_session("general", CONVERSATION, MIDDLE),
            ],
            store=self.store,
        )

        self.assertEqual(result, {"deleted": 1, "not_found": 0, "failed": 1})
        self.assertEqual(cli.deleted_ids(), [TIP])
        self.assertIn(ROOT, cli.sessions)
        self.assertIn(MIDDLE, cli.sessions)

    def test_a_parent_that_is_not_a_compression_predecessor_stays(self):
        cli = self.use_cli({
            ROOT: session("kore-general", end_reason="cli_exit"),
            TIP: session("kore-general", parent=ROOT),
        })
        result = bridge.forget(
            conversation_id=CONVERSATION,
            sessions=[bridge.sign_session("general", CONVERSATION, TIP)],
            store=self.store,
        )
        self.assertEqual(result, {"deleted": 1, "not_found": 0, "failed": 0})
        self.assertIn(ROOT, cli.sessions)

    def test_an_unreadable_chain_still_deletes_the_session_but_is_not_confirmed(self):
        cli = self.use_cli({TIP: session("kore-general")})
        cli.fail_actions.add("export")

        result = bridge.forget(
            conversation_id=CONVERSATION,
            sessions=[bridge.sign_session("general", CONVERSATION, TIP)],
            store=self.store,
        )

        self.assertEqual(result, {"deleted": 1, "not_found": 0, "failed": 1})
        self.assertEqual(cli.deleted_ids(), [TIP])

    def test_running_turns_of_the_chat_stop_and_its_buffer_is_dropped_first(self):
        cli = self.use_cli({TIP: session("kore-general")})
        done = self.finished_turn(scope="health")
        running = self.running_turn()
        other = self.running_turn(conversation=OTHER_CONVERSATION, pid=42_000)

        result = bridge.forget(
            conversation_id=CONVERSATION,
            sessions=[bridge.sign_session("general", CONVERSATION, TIP)],
            store=self.store,
        )

        self.assertEqual(result["deleted"], 1)
        self.assertEqual(running.status, "cancelled")
        self.assertIsNone(self.store.get(running.turn_id))
        self.assertIsNone(self.store.get(done.turn_id))
        self.assertIs(self.store.get(other.turn_id), other)
        self.assertEqual(other.status, "running")
        self.assertEqual([p.pid for p in self.terminated], [41_000])
        # Erst stoppen, dann Hermes-Sessions anfassen.
        self.assertEqual(self.log[0], ("terminate", 41_000))
        self.assertEqual(cli.deleted_ids(), [TIP])

    def test_a_chat_without_references_only_stops_and_drops_its_turns(self):
        cli = self.use_cli({TIP: session("kore-general")})
        running = self.running_turn()

        result = bridge.forget(conversation_id=CONVERSATION, sessions=[], store=self.store)

        self.assertEqual(result, {"deleted": 0, "not_found": 0, "failed": 0})
        self.assertIsNone(self.store.get(running.turn_id))
        self.assertEqual(cli.calls, [])

    def test_the_time_budget_reports_what_was_left_as_failed(self):
        cli = self.use_cli({TIP: session("kore-general"), MIDDLE: session("kore-general")})
        with patch.object(bridge, "FORGET_BUDGET_S", -1):
            result = bridge.forget(
                conversation_id=CONVERSATION,
                sessions=[bridge.sign_session("general", CONVERSATION, TIP),
                          bridge.sign_session("general", CONVERSATION, MIDDLE)],
                store=self.store,
            )
        self.assertEqual(result, {"deleted": 0, "not_found": 0, "failed": 2})
        self.assertEqual(cli.deleted_ids(), [])


class ForgetByScopeTests(ForgetTestCase):
    SESSIONS = {
        "20260901_070000_000001": session("kore-health", end_reason="compression"),
        "20260902_070000_000002": session("kore-health", parent="20260901_070000_000001"),
        "20260903_070000_000003": session("kore-health", ended=False),  # offen, z. B. nach Stopp
        "20260904_070000_000004": session("kore-general"),
        "20260905_070000_000005": session("telegram"),
        "20260906_070000_000006": session("cli", title="kore-health Notizen"),
    }

    def test_health_deletes_every_kore_health_session_and_nothing_else(self):
        cli = self.use_cli(self.SESSIONS)

        result = bridge.forget(scope="health", store=self.store)

        self.assertEqual(result, {"deleted": 3, "not_found": 0, "failed": 0})
        self.assertEqual(sorted(cli.sessions), [
            "20260904_070000_000004", "20260905_070000_000005", "20260906_070000_000006",
        ])
        for call in cli.calls:
            if call[4] == "prune":
                self.assertIn("--dry-run", call)
                self.assertEqual(call[call.index("--source") + 1], "kore-health")
                self.assertNotIn("--older-than", call)
            if call[4] == "list":
                self.assertEqual(call[call.index("--source") + 1], "kore-health")

    def test_all_deletes_both_kore_sources_only(self):
        cli = self.use_cli(self.SESSIONS)

        result = bridge.forget(scope="all", store=self.store)

        self.assertEqual(result, {"deleted": 4, "not_found": 0, "failed": 0})
        self.assertEqual(sorted(cli.sessions), ["20260905_070000_000005", "20260906_070000_000006"])

    def test_scope_forget_stops_only_turns_of_that_scope(self):
        self.use_cli({})
        health = self.running_turn(scope="health")
        general = self.running_turn(conversation=OTHER_CONVERSATION, pid=42_000)

        bridge.forget(scope="health", store=self.store)

        self.assertIsNone(self.store.get(health.turn_id))
        self.assertEqual(health.status, "cancelled")
        self.assertIs(self.store.get(general.turn_id), general)

        bridge.forget(scope="all", store=self.store)
        self.assertIsNone(self.store.get(general.turn_id))

    def test_a_crafted_preview_cannot_smuggle_a_foreign_id_into_the_deletion(self):
        # splitlines() trennt auch an U+2028, \x0b, \x1c-\x1e, \x85: eine Vorschauzeile, die so eine
        # fremde ID ans Zeilenende setzte, ergab frueher einen Loeschauftrag fuer diese Session.
        # Seit die Bridge Hermes-Ausgaben nur an \n trennt, wird die fremde ID gar nicht erst
        # gelesen; die Quellenpruefung je ID bleibt trotzdem (siehe Verfall).
        foreign = "20260905_070000_000005"
        cli = self.use_cli({
            "20260901_070000_000001": session("kore-health", ended=False,
                                              preview=f"Hallo\u2028{foreign}\u2028"),
            foreign: session("telegram"),
        })

        result = bridge.forget(scope="health", store=self.store)

        self.assertIn(foreign, cli.sessions)
        self.assertNotIn(foreign, cli.deleted_ids())
        self.assertNotIn(foreign, [call[-1] for call in cli.calls if call[4] == "export"])
        self.assertEqual(result, {"deleted": 1, "not_found": 0, "failed": 0})

    def test_a_line_separator_in_the_export_does_not_make_a_session_unforgettable(self):
        cli = self.use_cli({
            "20260901_070000_000001": session("kore-health", title="Schlaf\u2028Woche\u2029\x85"),
            "20260902_070000_000002": session("kore-health", ended=False, title="offen\u2028"),
        })

        result = bridge.forget(scope="health", store=self.store)

        self.assertEqual(result, {"deleted": 2, "not_found": 0, "failed": 0})
        self.assertEqual(cli.sessions, {})

    def test_pinned_archived_and_hidden_sessions_are_deleted_too(self):
        # Hermes `sessions prune` laesst archivierte und angeheftete Sessions ohne
        # --include-archived/--include-pinned aus; `list` zeigt archivierte und versteckte nie.
        cli = self.use_cli({
            "20260901_070000_000001": session("kore-health", pinned=True),
            "20260902_070000_000002": session("kore-health", archived=True),
            "20260903_070000_000003": session("kore-health", archived=True, hidden=True),
            "20260904_070000_000004": session("kore-health", archived=True, pinned=True),
            "20260905_070000_000005": session("kore-health", ended=False, pinned=True),
            "20260906_070000_000006": session("telegram", pinned=True, archived=True),
        })

        result = bridge.forget(scope="health", store=self.store)

        self.assertEqual(result, {"deleted": 5, "not_found": 0, "failed": 0})
        self.assertEqual(list(cli.sessions), ["20260906_070000_000006"])
        prunes = [call for call in cli.calls if call[4] == "prune"]
        self.assertEqual(len(prunes), 1)
        for flag in ("--include-archived", "--include-pinned", "--dry-run"):
            self.assertIn(flag, prunes[0])

    def test_an_open_session_hermes_hides_from_the_list_is_not_confirmed(self):
        # Offen UND archiviert (z. B. sessions.auto_archive nach einem Stopp) oder versteckt: `list`
        # zeigt sie nicht, `prune` loescht nur beendete. Die Bridge kennt ihre ID nicht und meldet
        # sie als failed statt einer falschen Bestaetigung.
        cli = self.use_cli({
            "20260901_070000_000001": session("kore-health"),
            "20260902_070000_000002": session("kore-health", ended=False),
            "20260903_070000_000003": session("kore-health", ended=False, archived=True),
            "20260904_070000_000004": session("kore-health", ended=False, hidden=True),
        })

        result = bridge.forget(scope="health", store=self.store)

        self.assertEqual(result, {"deleted": 2, "not_found": 0, "failed": 2})
        self.assertEqual(sorted(cli.sessions), ["20260903_070000_000003", "20260904_070000_000004"])

    def test_every_listed_id_is_checked_before_it_is_deleted(self):
        cli = self.use_cli(self.SESSIONS)

        bridge.forget(scope="health", store=self.store)

        for sid in cli.deleted_ids():
            export = [HERMES, "-p", PROFILE, "sessions", "export", "-", "--session-id", sid]
            delete = [HERMES, "-p", PROFILE, "sessions", "delete", "--yes", sid]
            self.assertLess(cli.calls.index(export), cli.calls.index(delete))

    def test_an_unreadable_listing_is_reported_as_failed(self):
        cli = self.use_cli(self.SESSIONS)
        cli.fail_actions.add("list")

        result = bridge.forget(scope="health", store=self.store)

        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["deleted"], 0)
        self.assertEqual(cli.deleted_ids(), [])


class ExpiryTests(ForgetTestCase):
    SESSIONS = {
        "20260801_070000_000001": session("kore-general", idle_days=31),
        "20260802_070000_000002": session("kore-health", idle_days=45),
        "20260920_070000_000003": session("kore-health", idle_days=4),
        "20260803_070000_000004": session("kore-health", idle_days=40, ended=False),
        "20260701_070000_000005": session("telegram", idle_days=90),
    }

    def test_prune_expired_selects_per_source_by_inactivity_and_deletes_each(self):
        cli = self.use_cli(self.SESSIONS)

        result = bridge.prune_expired(self.store)

        self.assertEqual(result, {"deleted": 2, "not_found": 0, "failed": 0})
        prunes = [call for call in cli.calls if call[4] == "prune"]
        self.assertEqual(prunes, [
            [HERMES, "-p", PROFILE, "sessions", "prune", "--older-than", "30",
             "--source", "kore-general", "--include-archived", "--include-pinned", "--dry-run"],
            [HERMES, "-p", PROFILE, "sessions", "prune", "--older-than", "30",
             "--source", "kore-health", "--include-archived", "--include-pinned", "--dry-run"],
        ])
        self.assertEqual(sorted(cli.deleted_ids()),
                         ["20260801_070000_000001", "20260802_070000_000002"])
        self.assertIn("20260920_070000_000003", cli.sessions)
        self.assertIn("20260701_070000_000005", cli.sessions)

    def test_expiry_never_deletes_a_listed_session_of_another_source(self):
        foreign = "20260701_070000_000005"
        cli = self.use_cli(self.SESSIONS)
        original = cli._prune

        def prune_with_a_foreign_line(rest):
            stdout, code = original(rest)
            lines = stdout.splitlines()
            source = rest[rest.index("--source") + 1]
            lines.insert(1, f"  {foreign}  2026-07-01 10:00  {source}")
            return "\n".join(lines) + "\n", code

        cli._prune = prune_with_a_foreign_line

        result = bridge.prune_expired(self.store)

        self.assertIn(foreign, cli.sessions)
        self.assertNotIn(foreign, cli.deleted_ids())
        self.assertEqual(result["deleted"], 2)
        self.assertEqual(result["failed"], 2)  # einmal je Quelle gelistet, nie geloescht

    def test_pinned_and_archived_sessions_expire_too(self):
        cli = self.use_cli({
            "20260801_070000_000001": session("kore-general", idle_days=31, pinned=True),
            "20260802_070000_000002": session("kore-health", idle_days=45, archived=True),
            "20260803_070000_000003": session("kore-health", idle_days=40, archived=True,
                                              pinned=True, hidden=True),
            "20260920_070000_000004": session("kore-health", idle_days=4, pinned=True),
        })

        result = bridge.prune_expired(self.store)

        self.assertEqual(result, {"deleted": 3, "not_found": 0, "failed": 0})
        self.assertEqual(list(cli.sessions), ["20260920_070000_000004"])

    def test_retention_days_come_from_the_configuration(self):
        cli = self.use_cli(self.SESSIONS)
        with patch.object(bridge, "SESSION_RETENTION_DAYS", 7):
            bridge.prune_expired(self.store)
        for call in cli.calls:
            if call[4] == "prune":
                self.assertEqual(call[call.index("--older-than") + 1], "7")

    def test_never_runs_while_a_turn_is_running(self):
        cli = self.use_cli(self.SESSIONS)
        self.running_turn()

        self.assertIsNone(bridge.prune_expired(self.store))
        self.assertEqual(cli.calls, [])

    def test_stops_when_a_turn_starts_in_the_middle(self):
        cli = self.use_cli(self.SESSIONS)
        original = cli._delete

        def delete_then_start_a_turn(rest):
            outcome = original(rest)
            if not self.store.active_count():
                self.running_turn()
            return outcome

        cli._delete = delete_then_start_a_turn

        self.assertIsNone(bridge.prune_expired(self.store))
        self.assertEqual(len(cli.deleted_ids()), 1)

    def test_schedule_runs_at_start_then_daily_and_retries_after_a_skip(self):
        cli = self.use_cli(self.SESSIONS)
        schedule = bridge.RetentionSchedule()

        self.assertTrue(schedule.tick(1_000.0, self.store))  # beim Start
        self.assertFalse(schedule.tick(1_000.0 + 3_600, self.store))
        turn = self.running_turn()
        self.assertFalse(schedule.tick(1_000.0 + 86_400, self.store))  # laufender Turn
        self.store.cancel(turn.turn_id)
        self.assertTrue(schedule.tick(1_000.0 + 86_460, self.store))  # naechste Minute
        self.assertEqual([call[4] for call in cli.calls].count("prune"), 4)

    def test_a_failing_hermes_does_not_stop_the_schedule(self):
        cli = self.use_cli(self.SESSIONS)
        cli.fail_actions.add("prune")
        schedule = bridge.RetentionSchedule()

        self.assertTrue(schedule.tick(1_000.0, self.store))
        self.assertEqual(cli.deleted_ids(), [])


class TurnStoreDropTests(unittest.TestCase):
    def setUp(self):
        self.store = bridge.TurnStore()
        patcher = patch("bridge._terminate_process_group", return_value=True)
        self.terminate = patcher.start()
        self.addCleanup(patcher.stop)

    def test_drop_ends_open_streams_with_cancelled_and_removes_the_turns(self):
        turn, _ = self.store.create(CONVERSATION, str(uuid.uuid4()), "health", origin_ticket="T" * 40)
        process = SimpleNamespace(pid=43_000)
        self.store.attach_process(turn, process)
        self.store.append_delta(turn, "Teil " + "T" * 10)

        self.assertEqual(self.store.drop(conversation_id=CONVERSATION), 1)

        events, finished = self.store.wait_events(turn, 0, 0)
        self.assertTrue(finished)
        self.assertEqual(events[-1][1]["code"], "cancelled")
        self.assertIsNone(self.store.get(turn.turn_id))
        self.assertEqual(self.store.active_count(), 0)
        self.terminate.assert_called_once_with(process)
        # Ein spaeter zurueckkehrender Worker aendert nichts mehr.
        self.store.finish(turn, bridge.TurnOutcome("spaet", False, None, "new", None, None))
        self.assertEqual(turn.status, "cancelled")

    def test_the_idempotency_index_forgets_the_dropped_turns(self):
        message_id = str(uuid.uuid4())
        first, _ = self.store.create(CONVERSATION, message_id, "general")
        self.store.drop(scope="all")
        second, is_new = self.store.create(CONVERSATION, message_id, "general")
        self.assertTrue(is_new)
        self.assertNotEqual(first.turn_id, second.turn_id)


class ForgetEndpointTests(unittest.TestCase):
    def setUp(self):
        for name, value in (("TOKEN", TEST_TOKEN), ("PROFILE", PROFILE), ("HERMES", HERMES)):
            patcher = patch.object(bridge, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.store = bridge.TurnStore()
        patcher = patch.object(bridge, "TURNS", self.store)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.cli = FakeSessionsCLI({TIP: session("kore-general")})
        patcher = patch("bridge._run_process_bounded", side_effect=self.cli)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.server = bridge.ThreadingHTTPServer(("127.0.0.1", 0), bridge.Handler)
        thread = threading.Thread(target=self.server.serve_forever, args=(0.01,), daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def post(self, payload, token=TEST_TOKEN, raw=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=5)
        body = raw if raw is not None else json.dumps(payload).encode("utf-8")
        headers = {"Content-Length": str(len(body))}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        connection.request("POST", "/coach/forget", body=body, headers=headers)
        response = connection.getresponse()
        data = json.loads(response.read())
        connection.close()
        return response.status, data

    def test_forget_by_reference_answers_with_the_three_counts(self):
        ref = bridge.sign_session("general", CONVERSATION, TIP)
        status, body = self.post({"conversation_id": CONVERSATION, "sessions": [ref]})
        self.assertEqual(status, 200)
        self.assertEqual(body, {"deleted": 1, "not_found": 0, "failed": 0})

    def test_forget_by_scope(self):
        status, body = self.post({"scope": "all"})
        self.assertEqual(status, 200)
        self.assertEqual(body, {"deleted": 1, "not_found": 0, "failed": 0})

    def test_requires_the_token(self):
        status, body = self.post({"scope": "all"}, token=None)
        self.assertEqual(status, 401)
        self.assertEqual(body["code"], "unauthorized")
        self.assertEqual(self.cli.calls, [])

    def test_rejects_malformed_bodies_without_touching_hermes(self):
        ref = bridge.sign_session("general", CONVERSATION, TIP)
        for payload in (
            {},
            {"scope": "general"},
            {"scope": "all", "conversation_id": CONVERSATION},
            {"sessions": [ref]},
            {"conversation_id": CONVERSATION.upper(), "sessions": [ref]},
            {"conversation_id": CONVERSATION, "sessions": ref},
            {"conversation_id": CONVERSATION, "sessions": [None]},
            {"conversation_id": CONVERSATION, "sessions": ["general:" + "x" * 130]},
            {"conversation_id": CONVERSATION, "sessions": [ref] * 17},
            {"conversation_id": CONVERSATION, "sessions": [ref], "extra": 1},
            [],
        ):
            with self.subTest(payload=payload):
                status, body = self.post(payload)
                self.assertEqual(status, 400)
                self.assertIn("error", body)
        self.assertEqual(self.cli.calls, [])

    def test_health_advertises_session_forget(self):
        self.assertIn("session_forget_v1", bridge.capabilities_payload()["capabilities"])


if __name__ == "__main__":
    unittest.main()
