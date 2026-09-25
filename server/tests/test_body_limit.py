"""Größenlimit vor Routing und Token-Prüfung (0.10 Task 28, Härtung vor der Veröffentlichung).

FastAPI liest den Body, bevor es die Token-Abhängigkeit auswertet. Ohne dieses Limit läge ein
beliebig großer Body eines Fremden im LAN im Arbeitsspeicher, bevor irgendwer „401" sagt.
"""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path


SERVER_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_ROOT))

from body_limit import MAX_BODY_BYTES, BodyLimitMiddleware  # noqa: E402


def run_app(app, *, headers=(), chunks=(b"",)):
    """Ein HTTP-Request durch eine ASGI-App; liefert (Status, Body, gelesene Bytes der App)."""
    messages = [
        {"type": "http.request", "body": chunk, "more_body": index < len(chunks) - 1}
        for index, chunk in enumerate(chunks)
    ]
    sent = []

    async def receive():
        return messages.pop(0) if messages else {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "method": "POST", "path": "/ingest", "headers": list(headers)}
    asyncio.run(app(scope, receive, send))
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return status, body


class ReadingApp:
    """Liest den ganzen Body wie FastAPI und antwortet 200 mit der Anzahl gelesener Bytes."""

    def __init__(self):
        self.read = 0

    async def __call__(self, scope, receive, send):
        while True:
            message = await receive()
            self.read += len(message.get("body", b""))
            if not message.get("more_body"):
                break
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": str(self.read).encode()})


class BodyLimitTests(unittest.TestCase):
    def test_the_ceiling_matches_the_apps_own_snapshot_limit(self) -> None:
        # Kore: RemoteSnapshotProjection.maximumSerializedBytes = 16 * 1_024 * 1_024
        self.assertEqual(MAX_BODY_BYTES, 16 * 1024 * 1024)

    def test_declared_oversize_is_rejected_without_reading_the_body(self) -> None:
        inner = ReadingApp()
        status, body = run_app(
            BodyLimitMiddleware(inner, max_bytes=10),
            headers=[(b"content-length", b"11")],
            chunks=(b"x" * 11,),
        )
        self.assertEqual(status, 413)
        self.assertEqual(inner.read, 0)
        self.assertNotIn(b"x", body)

    def test_undeclared_stream_is_cut_as_soon_as_it_passes_the_ceiling(self) -> None:
        inner = ReadingApp()
        status, _ = run_app(
            BodyLimitMiddleware(inner, max_bytes=10),
            chunks=(b"x" * 6, b"x" * 6, b"x" * 6),
        )
        self.assertEqual(status, 413)
        self.assertLessEqual(inner.read, 6)

    def test_a_body_at_the_ceiling_passes_unchanged(self) -> None:
        inner = ReadingApp()
        status, body = run_app(
            BodyLimitMiddleware(inner, max_bytes=10),
            headers=[(b"content-length", b"10")],
            chunks=(b"x" * 4, b"x" * 6),
        )
        self.assertEqual((status, body), (200, b"10"))

    def test_non_http_scopes_pass_through(self) -> None:
        seen = []

        async def inner(scope, receive, send):
            seen.append(scope["type"])

        asyncio.run(BodyLimitMiddleware(inner, max_bytes=1)({"type": "lifespan"}, None, None))
        self.assertEqual(seen, ["lifespan"])


if __name__ == "__main__":
    unittest.main()
