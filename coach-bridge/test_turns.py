"""Bridge-Turn-Auftraege 0.10 (Spec C §4.2–4.6, §10.1 Turn-Teil).

202 + turn_id, GET (loescht einen fertigen Turn genau einmal), SSE mit Last-Event-ID-Replay und
Keepalive, Stopp, Idempotenz ueber client_message_id. Hermes ist gestubbt; die HTTP-Tests laufen
gegen einen echten ThreadingHTTPServer auf 127.0.0.1, damit Streaming und Verbindungsabbruch echt sind.
"""
import http.client
import json
import threading
import time
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import patch

import bridge
from test_runner_stream_json import (
    CONVERSATION,
    OTHER_CONVERSATION,
    TEST_TOKEN,
    fixture_text,
    result_record,
    stream_lines,
)

THIRD_CONVERSATION = "123e4567-e89b-42d3-a456-426614174888"
TICKET = "TICKET-" + "a1b2c3d4" * 4
SESSION_ID = "20260924_120000_aaaaaa"


def new_id() -> str:
    return str(uuid.uuid4())


def wait_until(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    raise AssertionError("condition not reached in time")


class FakeHermes:
    """Steht fuer _run_process_bounded. `before` geht sofort raus, dann haelt der Lauf an, bis
    `release` gesetzt ist (oder die Prozessgruppe beendet wurde); danach kommen `after`."""

    def __init__(self, before=(), after=(), block=True, stderr="", returncode=0):
        self.before = list(before)
        self.after = list(after)
        self.block = block
        self.stderr = stderr
        self.returncode = returncode
        self.calls = []
        self.processes = []
        self.terminated = []
        self.started = threading.Event()
        self.release = threading.Event()
        self.lock = threading.Lock()

    def __call__(self, args, **kwargs):
        with self.lock:
            self.calls.append((args, kwargs))
            process = SimpleNamespace(pid=40_000 + len(self.calls))
            self.processes.append(process)
        if kwargs.get("on_process") is not None:
            try:
                kwargs["on_process"](process)
            except Exception as error:
                raise bridge.HermesInvocationError("Hermes process could not be registered") from error
        callback = kwargs.get("on_stdout_line")
        emitted = []
        for line in self.before:
            emitted.append(line)
            callback(line)
        self.started.set()
        if self.block:
            self.release.wait(5)
        if process in self.terminated:
            return SimpleNamespace(args=args, stdout="\n".join(emitted), stderr="", returncode=-15)
        for line in self.after:
            emitted.append(line)
            callback(line)
        return SimpleNamespace(
            args=args, stdout="\n".join(emitted), stderr=self.stderr, returncode=self.returncode
        )

    def terminate(self, process):
        self.terminated.append(process)
        self.release.set()
        return True


def records(*items):
    return stream_lines(*items).splitlines()


def text(value):
    return {"type": "text", "text": value}


class TicketRedactorTests(unittest.TestCase):
    def test_a_ticket_split_over_two_deltas_is_never_emitted(self):
        redactor = bridge.TicketRedactor(TICKET)
        out = redactor.feed("Plan: " + TICKET[:10])
        out += redactor.feed(TICKET[10:] + " fertig")
        out += redactor.flush()

        self.assertNotIn(TICKET, out)
        self.assertNotIn(TICKET[:10], out)
        self.assertEqual(out, "Plan: [REDACTED] fertig")

    def test_holds_back_at_most_len_ticket_minus_one_characters(self):
        redactor = bridge.TicketRedactor(TICKET)
        self.assertEqual(redactor.feed("Hallo " + TICKET[:5]), "Hallo ")
        self.assertEqual(redactor.flush(), TICKET[:5])
        # Ohne moeglichen Ticketanfang haelt der Puffer nichts zurueck.
        self.assertEqual(bridge.TicketRedactor(TICKET).feed("Hallo Welt"), "Hallo Welt")

    def test_without_ticket_text_passes_through(self):
        redactor = bridge.TicketRedactor(None)
        self.assertEqual(redactor.feed("a"), "a")
        self.assertEqual(redactor.flush(), "")


class TurnStoreTests(unittest.TestCase):
    def setUp(self):
        self.now = [1_000.0]
        self.store = bridge.TurnStore(clock=lambda: self.now[0])

    def finish(self, turn, reply="OK"):
        outcome = bridge.TurnOutcome(reply, False, None, "new", None, None)
        self.store.finish(turn, outcome)

    def test_same_client_message_id_returns_the_same_turn(self):
        first, is_new = self.store.create(CONVERSATION, "m1", "general")
        again, again_new = self.store.create(CONVERSATION, "m1", "general")

        self.assertTrue(is_new)
        self.assertFalse(again_new)
        self.assertIs(first, again)

    def test_second_message_in_a_running_chat_is_turn_in_progress(self):
        running, _ = self.store.create(CONVERSATION, "m1", "general")

        with self.assertRaises(bridge.TurnInProgress) as raised:
            self.store.create(CONVERSATION, "m2", "general")
        self.assertIs(raised.exception.turn, running)
        self.assertIs(self.store.running_for(CONVERSATION), running)

    def test_third_concurrent_turn_is_busy(self):
        self.store.create(CONVERSATION, "m1", "general")
        self.store.create(OTHER_CONVERSATION, "m1", "general")

        with self.assertRaises(bridge.TurnsBusy):
            self.store.create(THIRD_CONVERSATION, "m1", "general")

    def test_buffer_evicts_the_oldest_finished_turn(self):
        oldest = None
        for index in range(bridge.MAX_BUFFERED_TURNS):
            turn, _ = self.store.create(new_id(), "m", "general")
            self.now[0] += 1
            self.finish(turn)
            oldest = oldest or turn

        newest, _ = self.store.create(CONVERSATION, "m", "general")

        self.assertIsNone(self.store.get(oldest.turn_id))
        self.assertIs(self.store.get(newest.turn_id), newest)
        # Mit dem Turn verschwindet auch sein Idempotenz-Eintrag.
        _, is_new = self.store.create(oldest.conversation_id, "m", "general")
        self.assertTrue(is_new)

    def test_pop_if_done_removes_only_finished_turns_exactly_once(self):
        turn, _ = self.store.create(CONVERSATION, "m1", "general")
        self.assertIsNone(self.store.pop_if_done(turn.turn_id))
        self.assertIs(self.store.get(turn.turn_id), turn)

        self.finish(turn)

        self.assertIs(self.store.pop_if_done(turn.turn_id), turn)
        self.assertIsNone(self.store.pop_if_done(turn.turn_id))
        self.assertIsNone(self.store.get(turn.turn_id))

    def test_finished_turns_live_24_hours(self):
        turn, _ = self.store.create(CONVERSATION, "m1", "general")
        self.finish(turn)

        self.store.sweep(self.now[0] + bridge.TURN_TTL_S - 1)
        self.assertIs(self.store.get(turn.turn_id), turn)

        self.store.sweep(self.now[0] + bridge.TURN_TTL_S)
        self.assertIsNone(self.store.get(turn.turn_id))

    def test_sweep_ends_runs_over_the_deadline(self):
        turn, _ = self.store.create(CONVERSATION, "m1", "general")
        process = SimpleNamespace(pid=4242)
        self.store.attach_process(turn, process)

        with patch("bridge._terminate_process_group", return_value=True) as terminate:
            self.store.sweep(self.now[0] + bridge.turn_deadline_s() - 1)
            self.assertEqual(turn.status, "running")
            self.store.sweep(self.now[0] + bridge.turn_deadline_s() + 1)

        terminate.assert_called_once_with(process)
        self.assertEqual(turn.status, "failed")
        self.assertEqual(turn.error["code"], "timeout")
        self.assertIsNone(self.store.running_for(CONVERSATION))

    def test_a_finished_run_after_cancel_does_not_overwrite_the_status(self):
        turn, _ = self.store.create(CONVERSATION, "m1", "general", origin_ticket=None)
        self.store.append_delta(turn, "Teil")
        with patch("bridge._terminate_process_group", return_value=True):
            self.store.cancel(turn.turn_id)

        self.finish(turn, reply="Teil und mehr")

        self.assertEqual(turn.status, "cancelled")
        self.assertEqual(turn.reply, "Teil")

    def test_no_process_starts_after_cancel(self):
        turn, _ = self.store.create(CONVERSATION, "m1", "general")
        self.store.cancel(turn.turn_id)

        with self.assertRaises(bridge.TurnCancelled):
            self.store.attach_process(turn, SimpleNamespace(pid=1))


class TurnEndpointTests(unittest.TestCase):
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
        thread = threading.Thread(target=self.server.serve_forever, args=(0.01,), daemon=True)
        thread.start()
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
        # Loese einen haengenden Fake beim Aufraeumen, damit kein Worker liegen bleibt.
        self.addCleanup(fake.release.set)
        return fake

    def request(self, method, path, payload=None, headers=None, token=TEST_TOKEN):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        all_headers = {"Content-Length": str(len(body or b""))}
        if token is not None:
            all_headers["Authorization"] = f"Bearer {token}"
        all_headers.update(headers or {})
        connection.request(method, path, body=body, headers=all_headers)
        response = connection.getresponse()
        raw = response.read()
        connection.close()
        return response, raw

    def json_request(self, method, path, payload=None, **kwargs):
        response, raw = self.request(method, path, payload, **kwargs)
        return response.status, json.loads(raw)

    def start(self, conversation=CONVERSATION, message_id=None, message="Hallo", **extra):
        payload = {
            "message": message,
            "conversation_id": conversation,
            "health_context_allowed": False,
            "client_message_id": message_id or new_id(),
            **extra,
        }
        return self.json_request("POST", "/coach", payload)

    def wait_finished(self, turn_id):
        wait_until(lambda: self.store.get(turn_id) is not None
                   and self.store.get(turn_id).status != "running")

    def sse(self, turn_id, last_event_id=None):
        headers = {} if last_event_id is None else {"Last-Event-ID": str(last_event_id)}
        response, raw = self.request("GET", f"/coach/turns/{turn_id}/events", headers=headers)
        return response, raw.decode("utf-8")

    @staticmethod
    def sse_events(body):
        events = []
        for block in body.split("\n\n"):
            lines = [line for line in block.split("\n") if line and not line.startswith(":")]
            if not lines:
                continue
            fields = dict(line.split(": ", 1) for line in lines)
            events.append((int(fields["id"]), json.loads(fields["data"])))
        return events

    # ---- POST /coach mit client_message_id ------------------------------------------------

    def test_post_with_client_message_id_returns_202_and_runs_hermes_once(self):
        fake = self.use_hermes(FakeHermes(before=records(text("OK"), result_record("OK", SESSION_ID))))
        message_id = new_id()

        code, payload = self.start(message_id=message_id)
        again_code, again = self.start(message_id=message_id)

        self.assertEqual(code, 202)
        self.assertEqual(set(payload), {"turn_id", "status"})
        self.assertTrue(bridge.valid_conversation_id(payload["turn_id"]))
        self.assertEqual(payload["status"], "running")
        self.assertEqual(again_code, 202)
        self.assertEqual(again["turn_id"], payload["turn_id"])
        fake.started.wait(2)
        fake.release.set()
        self.wait_finished(payload["turn_id"])
        self.assertEqual(len(fake.calls), 1)
        # Auch nach dem Ende bleibt die Wiederholung an denselben Turn gebunden.
        _, after_done = self.start(message_id=message_id)
        self.assertEqual(after_done, {"turn_id": payload["turn_id"], "status": "done"})
        self.assertEqual(len(fake.calls), 1)

    def test_second_message_in_a_running_chat_is_409_with_the_running_turn(self):
        self.use_hermes(FakeHermes())
        _, first = self.start()

        code, payload = self.start()

        self.assertEqual(code, 409)
        self.assertEqual(payload["code"], "turn_in_progress")
        self.assertEqual(payload["turn_id"], first["turn_id"])

    def test_third_concurrent_turn_is_429_busy(self):
        self.use_hermes(FakeHermes())
        self.assertEqual(self.start(CONVERSATION)[0], 202)
        self.assertEqual(self.start(OTHER_CONVERSATION)[0], 202)

        code, payload = self.start(THIRD_CONVERSATION)

        self.assertEqual(code, 429)
        self.assertEqual(payload["code"], "busy")

    def test_invalid_client_message_id_is_rejected_before_any_run(self):
        fake = self.use_hermes(FakeHermes(block=False))
        for bad in ("m1", "123E4567-E89B-42D3-A456-426614174000", None, 7):
            with self.subTest(bad=bad):
                code, _ = self.json_request("POST", "/coach", {
                    "message": "Hallo",
                    "conversation_id": CONVERSATION,
                    "health_context_allowed": False,
                    "client_message_id": bad,
                })
                self.assertEqual(code, 400)
        self.assertEqual(fake.calls, [])

    def test_prompt_too_large_is_413_before_a_turn_exists(self):
        fake = self.use_hermes(FakeHermes(block=False))
        with patch.object(bridge, "MAX_PROMPT_BYTES", 4_096):
            code, payload = self.start(message="x" * 5_000)

        self.assertEqual(code, 413)
        self.assertEqual(payload["code"], "prompt_too_large")
        self.assertEqual(fake.calls, [])
        self.assertEqual(bridge.health_payload()["active_turns"], 0)

    def test_argv_transport_is_503_transport_unavailable(self):
        self.use_hermes(FakeHermes(block=False))
        with patch.object(bridge, "PROMPT_TRANSPORT", "argv"):
            code, payload = self.start()

        self.assertEqual(code, 503)
        self.assertEqual(payload["code"], "transport_unavailable")

    def test_consent_contract_still_applies_on_the_turn_path(self):
        fake = self.use_hermes(FakeHermes(block=False))
        code, _ = self.start(context={"readiness": 70})

        self.assertEqual(code, 400)
        self.assertEqual(fake.calls, [])

    # ---- GET /coach/turns/{id} --------------------------------------------------------------

    def test_get_delivers_a_finished_turn_once_then_404(self):
        self.use_hermes(FakeHermes(
            before=records(text("O"), text("K"), result_record("OK", SESSION_ID)), block=False
        ))
        _, started = self.start()
        turn_id = started["turn_id"]
        self.wait_finished(turn_id)

        code, payload = self.json_request("GET", f"/coach/turns/{turn_id}")
        second_code, second = self.json_request("GET", f"/coach/turns/{turn_id}")

        self.assertEqual(code, 200)
        self.assertEqual(payload["turn_id"], turn_id)
        self.assertEqual(payload["conversation_id"], CONVERSATION)
        self.assertEqual(payload["status"], "done")
        self.assertEqual(payload["reply"], "OK")
        self.assertIs(payload["used_kore"], False)
        self.assertEqual(payload["context"], "new")
        self.assertEqual(bridge.verify_session(payload["session"], "general", CONVERSATION), SESSION_ID)
        self.assertNotIn("reset_reason", payload)
        self.assertNotIn("error", payload)
        self.assertEqual(second_code, 404)
        self.assertEqual(second["code"], "turn_unknown")

    def test_get_on_a_running_turn_does_not_delete_it(self):
        fake = self.use_hermes(FakeHermes(before=records(text("Teil"))))
        _, started = self.start()
        fake.started.wait(2)

        code, payload = self.json_request("GET", f"/coach/turns/{started['turn_id']}")

        self.assertEqual(code, 200)
        self.assertEqual(payload["status"], "running")
        self.assertNotIn("reply", payload)
        self.assertIsNotNone(self.store.get(started["turn_id"]))

    def test_failed_turn_reports_the_error_code_without_diagnostics(self):
        self.use_hermes(FakeHermes(
            before=records(text("Teil")), block=False, stderr="provider secret: top-secret", returncode=3
        ))
        _, started = self.start()
        self.wait_finished(started["turn_id"])

        code, payload = self.json_request("GET", f"/coach/turns/{started['turn_id']}")

        self.assertEqual(code, 200)
        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["error"], {
            "code": "hermes_failed", "message": bridge.TURN_ERRORS["hermes_failed"][1],
        })
        self.assertEqual(payload["reply"], "Teil")
        self.assertNotIn("top-secret", json.dumps(payload))

    def test_strict_turn_paths(self):
        valid = new_id()
        for path in (
            "/coach/turns/not-a-uuid",
            f"/coach/turns/{valid.upper()}",
            f"/coach/turns/{valid}/",
            f"/coach/turns/{valid}/events/x",
            f"/coach/turns/{valid}?x=1",
            f"/coach/turns/{valid}/cancel",
            f"/coach/turns/{valid.replace('-', '')}",
        ):
            with self.subTest(path=path):
                code, payload = self.json_request("GET", path)
                self.assertEqual(code, 404)
                self.assertNotEqual(payload.get("code"), "turn_unknown")
        code, payload = self.json_request("GET", f"/coach/turns/{valid}")
        self.assertEqual((code, payload["code"]), (404, "turn_unknown"))

    def test_turn_endpoints_require_the_token(self):
        turn_id = new_id()
        for method, path in (
            ("GET", f"/coach/turns/{turn_id}"),
            ("GET", f"/coach/turns/{turn_id}/events"),
            ("POST", f"/coach/turns/{turn_id}/cancel"),
        ):
            with self.subTest(path=path):
                code, payload = self.json_request(method, path, token="wrong")
                self.assertEqual(code, 401)
                self.assertEqual(payload["code"], "unauthorized")

    # ---- SSE ---------------------------------------------------------------------------------

    def test_sse_streams_numbered_text_deltas_then_done_and_closes(self):
        self.use_hermes(FakeHermes(
            before=[line for line in fixture_text("synthetic-stream-tool-use.jsonl").splitlines()],
            block=False,
        ))
        _, started = self.start()
        turn_id = started["turn_id"]
        self.wait_finished(turn_id)

        response, body = self.sse(turn_id)

        self.assertEqual(response.status, 200)
        self.assertEqual(response.getheader("Content-Type"), "text/event-stream; charset=utf-8")
        self.assertEqual(response.getheader("Cache-Control"), "no-cache")
        events = self.sse_events(body)
        self.assertEqual([n for n, _ in events], [1, 2, 3])
        self.assertEqual([e for _, e in events[:2]], [{"delta": "Plan "}, {"delta": "vorgeschlagen."}])
        done = events[-1][1]
        self.assertEqual(done["done"], True)
        self.assertIs(done["used_kore"], False)
        self.assertEqual(done["context"], "new")
        self.assertIn("session", done)
        self.assertNotIn("reset_reason", done)
        # Werkzeug-Ereignisse bleiben in der Bridge.
        for secret in ("SYNTHETIC-TOOL-INPUT-TICKET", "SYNTHETIC-TOOL-OUTPUT", "tool_use", "propose"):
            self.assertNotIn(secret, body)
        # SSE loescht nie.
        self.assertIsNotNone(self.store.get(turn_id))
        self.assertEqual(self.json_request("GET", f"/coach/turns/{turn_id}")[0], 200)

    def test_sse_replays_only_events_after_last_event_id(self):
        self.use_hermes(FakeHermes(
            before=records(text("A"), text("B"), text("C"), result_record("ABC", SESSION_ID)),
            block=False,
        ))
        _, started = self.start()
        self.wait_finished(started["turn_id"])

        _, body = self.sse(started["turn_id"], last_event_id=2)

        events = self.sse_events(body)
        self.assertEqual([n for n, _ in events], [3, 4])
        self.assertEqual(events[0][1], {"delta": "C"})
        self.assertTrue(events[1][1]["done"])

    def test_invalid_last_event_id_is_400(self):
        self.use_hermes(FakeHermes(block=False))
        _, started = self.start()
        response, _ = self.sse(started["turn_id"], last_event_id="abc")
        self.assertEqual(response.status, 400)

    def test_sse_follows_a_running_turn_live_with_keepalive(self):
        fake = self.use_hermes(FakeHermes(
            before=records(text("Hallo")),
            after=records(text(" Welt"), result_record("Hallo Welt", SESSION_ID)),
        ))
        _, started = self.start()
        fake.started.wait(2)
        threading.Timer(0.3, fake.release.set).start()

        with patch.object(bridge, "KEEPALIVE_S", 0.05):
            _, body = self.sse(started["turn_id"])

        self.assertIn(": keepalive\n\n", body)
        events = self.sse_events(body)
        self.assertEqual([e for _, e in events[:2]], [{"delta": "Hallo"}, {"delta": " Welt"}])
        self.assertTrue(events[-1][1]["done"])

    def test_app_disconnect_does_not_touch_the_worker(self):
        fake = self.use_hermes(FakeHermes(
            before=records(text("Hallo")),
            after=records(text(" Welt"), result_record("Hallo Welt", SESSION_ID)),
        ))
        _, started = self.start()
        turn_id = started["turn_id"]
        fake.started.wait(2)
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        connection.request("GET", f"/coach/turns/{turn_id}/events",
                           headers={"Authorization": f"Bearer {TEST_TOKEN}"})
        response = connection.getresponse()
        self.assertEqual(response.readline(), b"id: 1\n")
        connection.close()

        fake.release.set()
        self.wait_finished(turn_id)

        self.assertEqual(fake.terminated, [])
        code, payload = self.json_request("GET", f"/coach/turns/{turn_id}")
        self.assertEqual((code, payload["status"], payload["reply"]), (200, "done", "Hallo Welt"))

    def test_error_turn_ends_the_stream_with_error_and_code(self):
        self.use_hermes(FakeHermes(before=records(text("Teil")), block=False, returncode=3))
        _, started = self.start()
        self.wait_finished(started["turn_id"])

        _, body = self.sse(started["turn_id"])

        events = self.sse_events(body)
        self.assertEqual(events[-1][1], {
            "error": bridge.TURN_ERRORS["hermes_failed"][1], "code": "hermes_failed",
        })

    def test_final_reply_without_deltas_is_streamed_before_done(self):
        self.use_hermes(FakeHermes(before=records(result_record("Nur Ergebnis", SESSION_ID)), block=False))
        _, started = self.start()
        self.wait_finished(started["turn_id"])

        _, body = self.sse(started["turn_id"])

        events = self.sse_events(body)
        self.assertEqual(events[0][1], {"delta": "Nur Ergebnis"})
        self.assertTrue(events[1][1]["done"])

    # ---- Redaction -------------------------------------------------------------------------

    def test_ticket_split_over_two_deltas_never_reaches_stream_or_reply(self):
        self.use_hermes(FakeHermes(
            before=records(
                text("Plan-ID " + TICKET[:9]),
                text(TICKET[9:] + " ist angelegt."),
                result_record("Plan-ID " + TICKET + " ist angelegt.", SESSION_ID),
            ),
            block=False,
        ))
        with patch("bridge.origin_ticket_for", return_value=TICKET):
            code, started = self.start(
                health_context_allowed=True,
                consent_scope_version=bridge.CONSENT_SCOPE_VERSION,
            )
        self.assertEqual(code, 202)
        self.wait_finished(started["turn_id"])

        _, body = self.sse(started["turn_id"])
        _, payload = self.json_request("GET", f"/coach/turns/{started['turn_id']}")

        self.assertNotIn(TICKET, body)
        self.assertNotIn(TICKET[:9], body)
        self.assertNotIn(TICKET, json.dumps(payload))
        streamed = "".join(e["delta"] for _, e in self.sse_events(body) if "delta" in e)
        self.assertEqual(streamed, "Plan-ID [REDACTED] ist angelegt.")
        self.assertEqual(payload["reply"], "Plan-ID [REDACTED] ist angelegt.")
        # Mit Ticket kommt kore in die Werkzeugliste.
        args = bridge._run_process_bounded.call_args.args[0]
        self.assertIn("kore", args[args.index("-t") + 1].split(","))

    # ---- Cancel ----------------------------------------------------------------------------

    def test_cancel_ends_the_process_group_keeps_the_text_and_is_idempotent(self):
        fake = self.use_hermes(FakeHermes(
            before=records(text("Bisher")),
            after=records(text(" und mehr"), result_record("Bisher und mehr", SESSION_ID)),
        ))
        _, started = self.start()
        turn_id = started["turn_id"]
        fake.started.wait(2)

        code, payload = self.json_request("POST", f"/coach/turns/{turn_id}/cancel")
        again_code, again = self.json_request("POST", f"/coach/turns/{turn_id}/cancel")

        self.assertEqual((code, payload), (200, {"turn_id": turn_id, "status": "cancelled"}))
        self.assertEqual((again_code, again), (200, {"turn_id": turn_id, "status": "cancelled"}))
        self.assertEqual(fake.terminated, [fake.processes[0]])
        self.assertEqual(self.store.get(turn_id).pgid, fake.processes[0].pid)
        wait_until(lambda: len(fake.calls) == 1 and not any(
            t.is_alive() for t in threading.enumerate() if t.name.startswith("kore-turn-")
        ))
        _, body = self.sse(turn_id)
        events = self.sse_events(body)
        self.assertEqual(events[0][1], {"delta": "Bisher"})
        self.assertEqual(events[-1][1]["code"], "cancelled")
        get_code, result = self.json_request("GET", f"/coach/turns/{turn_id}")
        self.assertEqual((get_code, result["status"], result["reply"]), (200, "cancelled", "Bisher"))
        self.assertEqual(result["error"]["code"], "cancelled")
        # Der Chat ist wieder frei.
        self.assertEqual(self.start()[0], 202)

    def test_cancel_of_an_unknown_turn_is_404(self):
        code, payload = self.json_request("POST", f"/coach/turns/{new_id()}/cancel")
        self.assertEqual((code, payload["code"]), (404, "turn_unknown"))

    def test_cancel_of_a_finished_turn_changes_nothing(self):
        fake = self.use_hermes(FakeHermes(
            before=records(text("OK"), result_record("OK", SESSION_ID)), block=False
        ))
        _, started = self.start()
        self.wait_finished(started["turn_id"])

        code, payload = self.json_request("POST", f"/coach/turns/{started['turn_id']}/cancel")

        self.assertEqual((code, payload["status"]), (200, "done"))
        self.assertEqual(fake.terminated, [])

    # ---- /health -----------------------------------------------------------------------------

    def test_health_reports_detached_turns_buffer_ttl_and_active_turns(self):
        fake = self.use_hermes(FakeHermes())
        self.assertIn("detached_turns_v1", bridge.capabilities_payload()["capabilities"])
        self.assertEqual(bridge.health_payload()["turn_buffer_ttl_hours"], 24)
        self.assertEqual(bridge.health_payload()["active_turns"], 0)

        _, started = self.start()
        fake.started.wait(2)
        code, health = self.json_request("GET", "/health")

        self.assertEqual(code, 200)
        self.assertEqual(health["active_turns"], 1)
        fake.release.set()
        self.wait_finished(started["turn_id"])
        self.assertEqual(bridge.health_payload()["active_turns"], 0)


if __name__ == "__main__":
    unittest.main()
