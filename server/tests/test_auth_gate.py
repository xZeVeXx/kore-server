"""Bearer-Pruefung in der ASGI-Schicht, bevor irgendwer den Body liest (0.13 S11b).

FastAPI parst den JSON-Body (bis 16 MiB) vor der Token-Abhaengigkeit: ein einziger Request ohne
Token kostete ~400 MiB RSS und blockierte die Event-Loop. Die Gate-Schicht antwortet 401, ohne
`receive` auch nur einmal aufzurufen.
"""

from __future__ import annotations

import asyncio
import re
import sys
import unittest
from pathlib import Path


SERVER_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_ROOT))

from auth_gate import ANONYMOUS_MAX_BODY_BYTES, BearerGateMiddleware  # noqa: E402

GOOD = "Bearer good-token"
PROTECTED = re.compile(r"/(?:ingest|plan/pending|plan/[^/]+/ack)/?").fullmatch


def authorize(value: str) -> bool:
    return value == GOOD


class CountingReceive:
    """Ein Upload in Stuecken; zaehlt, wie oft und wie viel die App tatsaechlich liest."""

    def __init__(self, chunks: int, chunk_size: int = 64 * 1024) -> None:
        self.remaining = chunks
        self.chunk_size = chunk_size
        self.calls = 0
        self.bytes_read = 0

    async def __call__(self):
        self.calls += 1
        if self.remaining <= 0:
            return {"type": "http.disconnect"}
        self.remaining -= 1
        self.bytes_read += self.chunk_size
        return {
            "type": "http.request",
            "body": b"x" * self.chunk_size,
            "more_body": self.remaining > 0,
        }


class ReadingApp:
    """Liest den ganzen Body wie FastAPI und antwortet 200."""

    def __init__(self) -> None:
        self.called = False
        self.seen_headers: list = []

    async def __call__(self, scope, receive, send):
        self.called = True
        if scope["type"] != "http":
            return
        while True:
            message = await receive()
            if not message.get("more_body"):
                break
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})


def call(app, *, path="/ingest", method="POST", headers=(), receive=None, scope_type="http"):
    receive = receive or CountingReceive(0)
    sent: list = []

    async def send(message):
        sent.append(message)

    scope = {"type": scope_type, "method": method, "path": path, "headers": list(headers)}
    asyncio.run(app(scope, receive, send))
    start = next((m for m in sent if m["type"] == "http.response.start"), None)
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return (start["status"] if start else None), dict(start["headers"]) if start else {}, body, receive


def gate(inner=None, **options):
    inner = inner or ReadingApp()
    return inner, BearerGateMiddleware(inner, authorize=authorize, protected=PROTECTED, **options)


def bearer(value: str = GOOD):
    return [(b"authorization", value.encode("latin-1"))]


class GateRejectsBeforeReadingTests(unittest.TestCase):
    def assert_unauthorized_without_reading(self, headers, path="/ingest", method="POST") -> None:
        inner, app = gate()
        upload = CountingReceive(chunks=256)  # 16 MiB
        status, response_headers, body, _ = call(
            app, path=path, method=method, headers=headers, receive=upload
        )
        self.assertEqual(status, 401)
        self.assertEqual(body, b'{"detail":"Unauthorized"}')
        self.assertEqual(response_headers[b"www-authenticate"], b"Bearer")
        self.assertEqual(response_headers[b"content-type"], b"application/json")
        # Close the connection so the sender stops streaming the unread body.
        self.assertEqual(response_headers[b"connection"], b"close")
        self.assertEqual(int(response_headers[b"content-length"]), len(body))
        self.assertEqual(upload.calls, 0, "the gate must not read a single body message")
        self.assertEqual(upload.bytes_read, 0)
        self.assertFalse(inner.called)

    def test_wrong_token_with_a_16_mib_body_is_401_and_nothing_is_read(self) -> None:
        self.assert_unauthorized_without_reading(
            [(b"authorization", b"Bearer wrong"), (b"content-length", str(16 * 1024 * 1024).encode())]
        )

    def test_missing_token_with_a_large_body_is_401_and_nothing_is_read(self) -> None:
        self.assert_unauthorized_without_reading([(b"content-length", b"16000000")])

    def test_every_protected_route_is_gated(self) -> None:
        cases = (
            ("/ingest", "POST"),
            ("/plan/pending", "GET"),
            ("/plan/9f1c2d3e-aaaa-bbbb-cccc-0123456789ab/ack", "POST"),
            ("/ingest/", "POST"),
        )
        for path, method in cases:
            with self.subTest(path=path):
                self.assert_unauthorized_without_reading([], path=path, method=method)

    def test_other_methods_on_a_protected_path_are_gated_too(self) -> None:
        for method in ("PUT", "DELETE", "OPTIONS", "HEAD", "GET"):
            with self.subTest(method=method):
                self.assert_unauthorized_without_reading([], method=method)

    def test_a_non_bearer_scheme_and_an_empty_value_are_401(self) -> None:
        for value in (b"", b"Basic good-token", b"good-token", b"bearer good-token"):
            with self.subTest(value=value):
                self.assert_unauthorized_without_reading([(b"authorization", value)])

    def test_two_authorization_headers_are_ambiguous_and_refused(self) -> None:
        # The valid one first: a proxy-style duplicate must not let the second one ride along.
        self.assert_unauthorized_without_reading(bearer() + [(b"authorization", b"Bearer other")])

    def test_header_bytes_are_decoded_as_latin1_like_the_framework_does(self) -> None:
        seen = []

        def spy(value: str) -> bool:
            seen.append(value)
            return False

        app = BearerGateMiddleware(ReadingApp(), authorize=spy, protected=PROTECTED)
        call(app, headers=[(b"authorization", "Bearer töken".encode("latin-1"))])
        self.assertEqual(seen, ["Bearer töken"])


class GateLetsValidRequestsThroughTests(unittest.TestCase):
    def test_a_valid_token_reaches_the_app_which_reads_the_whole_body(self) -> None:
        inner, app = gate()
        upload = CountingReceive(chunks=8)
        status, _, body, _ = call(app, headers=bearer(), receive=upload)
        self.assertEqual((status, body), (200, b"ok"))
        self.assertTrue(inner.called)
        self.assertEqual(upload.bytes_read, 8 * 64 * 1024)

    def test_the_decision_belongs_to_the_authorize_callable(self) -> None:
        # A device token is just another value the callable accepts.
        app = BearerGateMiddleware(
            ReadingApp(), authorize=lambda value: value == "Bearer device-token", protected=PROTECTED
        )
        status, *_ = call(app, headers=[(b"authorization", b"Bearer device-token")])
        self.assertEqual(status, 200)
        status, *_ = call(app, headers=bearer())
        self.assertEqual(status, 401)

    def test_non_http_scopes_pass_straight_through(self) -> None:
        inner, app = gate()
        call(app, scope_type="lifespan")
        self.assertTrue(inner.called)


class AnonymousBodyCapTests(unittest.TestCase):
    def test_an_unprotected_path_caps_the_body_at_a_few_kib(self) -> None:
        self.assertLessEqual(ANONYMOUS_MAX_BODY_BYTES, 16 * 1024)
        self.assertGreaterEqual(ANONYMOUS_MAX_BODY_BYTES, 1024)

    def test_declared_content_length_over_the_cap_is_413_without_reading(self) -> None:
        inner, app = gate()
        upload = CountingReceive(chunks=256)
        status, _, _, _ = call(
            app,
            path="/health",
            method="POST",
            headers=[(b"content-length", str(16 * 1024 * 1024).encode())],
            receive=upload,
        )
        self.assertEqual(status, 413)
        self.assertEqual(upload.calls, 0)
        self.assertFalse(inner.called)

    def test_a_stream_without_content_length_is_cut_at_the_cap(self) -> None:
        inner, app = gate()
        upload = CountingReceive(chunks=256)
        status, *_ = call(app, path="/nope", method="POST", receive=upload)
        self.assertEqual(status, 413)
        self.assertLess(upload.bytes_read, 16 * 1024 * 1024)

    def test_a_small_unauthenticated_request_still_reaches_the_app(self) -> None:
        inner, app = gate()
        status, *_ = call(app, path="/health", method="GET")
        self.assertEqual(status, 200)
        self.assertTrue(inner.called)

    def test_a_valid_token_keeps_the_large_ceiling_of_the_inner_layer(self) -> None:
        # 256 KiB is far over the anonymous cap but a normal snapshot size.
        inner, app = gate()
        upload = CountingReceive(chunks=4)
        status, *_ = call(app, headers=bearer(), receive=upload)
        self.assertEqual(status, 200)


if __name__ == "__main__":
    unittest.main()
