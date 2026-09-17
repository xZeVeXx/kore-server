"""The Origin guard: policy, parsing, and both live surfaces."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SERVER_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_ROOT))

from origin_guard import (  # noqa: E402
    ALLOWED_ORIGINS_ENV,
    OriginGuardMiddleware,
    allowed_origins_from_env,
    normalize_origin,
    origin_is_allowed,
)


class OriginPolicyTests(unittest.TestCase):
    def test_absent_origin_passes_because_that_is_every_intended_client(self) -> None:
        # The iOS app, the hermes CLI and curl send no Origin. An empty allowlist must
        # not lock them out — it only means "no browser".
        self.assertTrue(origin_is_allowed(None, frozenset()))

    def test_any_present_origin_is_refused_against_an_empty_allowlist(self) -> None:
        self.assertFalse(origin_is_allowed("http://evil.example", frozenset()))
        self.assertFalse(origin_is_allowed("http://localhost:8081", frozenset()))

    def test_listed_origin_passes_and_an_unlisted_one_does_not(self) -> None:
        allowed = frozenset({"http://localhost:5173"})
        self.assertTrue(origin_is_allowed("http://localhost:5173", allowed))
        self.assertFalse(origin_is_allowed("http://localhost:5174", allowed))

    def test_a_different_scheme_or_port_is_a_different_origin(self) -> None:
        allowed = frozenset({"http://10.0.0.10:8081"})
        self.assertFalse(origin_is_allowed("https://10.0.0.10:8081", allowed))
        self.assertFalse(origin_is_allowed("http://10.0.0.10:8000", allowed))
        self.assertFalse(origin_is_allowed("http://10.0.0.10", allowed))

    def test_the_literal_null_origin_is_not_special_cased(self) -> None:
        # Sandboxed iframes and some file:// pages send Origin: null. It is a wildcard in
        # practice and must never pass by accident.
        self.assertFalse(origin_is_allowed("null", frozenset()))

    def test_case_and_one_trailing_slash_are_normalized_on_both_sides(self) -> None:
        self.assertEqual(normalize_origin("  HTTP://LocalHost:5173/  "), "http://localhost:5173")
        allowed = frozenset({normalize_origin("HTTP://LocalHost:5173/")})
        self.assertTrue(origin_is_allowed("http://localhost:5173", allowed))


class AllowlistParsingTests(unittest.TestCase):
    def test_missing_variable_yields_the_empty_allowlist(self) -> None:
        self.assertEqual(allowed_origins_from_env({}), frozenset())

    def test_empty_and_whitespace_entries_are_dropped_not_stored(self) -> None:
        # A stray comma must not put "" into the set, or an empty Origin header would pass.
        parsed = allowed_origins_from_env({ALLOWED_ORIGINS_ENV: " , ,, "})
        self.assertEqual(parsed, frozenset())

    def test_several_origins_are_split_trimmed_and_normalized(self) -> None:
        parsed = allowed_origins_from_env(
            {ALLOWED_ORIGINS_ENV: "http://localhost:5173 , HTTPS://Kore.example/"}
        )
        self.assertEqual(parsed, frozenset({"http://localhost:5173", "https://kore.example"}))


class _Sink:
    """Minimal downstream app that records whether it was reached at all."""

    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, scope, receive, send) -> None:
        self.calls += 1
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})


async def _drive(middleware, headers, scope_type="http"):
    messages: list[dict] = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    await middleware({"type": scope_type, "headers": headers}, receive, send)
    return messages


class MiddlewareTests(unittest.IsolatedAsyncioTestCase):
    async def test_request_without_origin_reaches_the_application(self) -> None:
        sink = _Sink()
        messages = await _drive(OriginGuardMiddleware(sink, []), [(b"accept", b"*/*")])
        self.assertEqual(sink.calls, 1)
        self.assertEqual(messages[0]["status"], 200)

    async def test_foreign_origin_is_refused_before_the_application_runs(self) -> None:
        sink = _Sink()
        messages = await _drive(
            OriginGuardMiddleware(sink, []),
            [(b"origin", b"http://evil.example")],
        )
        self.assertEqual(sink.calls, 0)
        self.assertEqual(messages[0]["status"], 403)

    async def test_the_rejection_body_does_not_echo_the_origin_back(self) -> None:
        sink = _Sink()
        messages = await _drive(
            OriginGuardMiddleware(sink, []),
            [(b"origin", b"http://evil.example")],
        )
        body = b"".join(m.get("body", b"") for m in messages)
        self.assertNotIn(b"evil.example", body)
        self.assertEqual(body, b'{"detail":"Forbidden origin"}')

    async def test_an_empty_origin_header_is_treated_as_present_and_refused(self) -> None:
        sink = _Sink()
        messages = await _drive(OriginGuardMiddleware(sink, []), [(b"origin", b"")])
        self.assertEqual(sink.calls, 0)
        self.assertEqual(messages[0]["status"], 403)

    async def test_two_origin_headers_are_ambiguous_and_refused(self) -> None:
        sink = _Sink()
        messages = await _drive(
            OriginGuardMiddleware(sink, ["http://localhost:5173"]),
            [(b"origin", b"http://localhost:5173"), (b"origin", b"http://evil.example")],
        )
        self.assertEqual(sink.calls, 0)
        self.assertEqual(messages[0]["status"], 403)

    async def test_listed_origin_passes_through_the_middleware(self) -> None:
        sink = _Sink()
        messages = await _drive(
            OriginGuardMiddleware(sink, ["http://localhost:5173"]),
            [(b"origin", b"http://localhost:5173")],
        )
        self.assertEqual(sink.calls, 1)
        self.assertEqual(messages[0]["status"], 200)

    async def test_non_http_scopes_are_passed_through_untouched(self) -> None:
        sink = _Sink()
        await _drive(OriginGuardMiddleware(sink, []), [], scope_type="lifespan")
        self.assertEqual(sink.calls, 1)


if __name__ == "__main__":
    unittest.main()
