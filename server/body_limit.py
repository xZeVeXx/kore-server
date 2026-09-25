"""Body-size ceiling for the ingest API, in front of routing, parsing and the token check.

FastAPI reads and buffers a request body before it resolves dependencies, so the bearer check
on `/ingest` only runs after the whole body sits in memory. This layer rejects anything above
the ceiling first: by `Content-Length` when declared, by counting the stream otherwise.

The ceiling is the app's own limit for a serialized snapshot
(`RemoteSnapshotProjection.maximumSerializedBytes` = 16 MiB), so no legitimate upload is cut.
"""

from __future__ import annotations

from starlette.exceptions import HTTPException

MAX_BODY_BYTES = 16 * 1024 * 1024

_TOO_LARGE_BODY = b'{"detail":"Request too large"}'


class _BodyTooLarge(HTTPException):
    """Raised from `receive`; FastAPI re-raises HTTPExceptions from body parsing unchanged."""

    def __init__(self) -> None:
        super().__init__(status_code=413, detail="Request too large")


async def _reject(send) -> None:
    await send({
        "type": "http.response.start",
        "status": 413,
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(_TOO_LARGE_BODY)).encode("ascii")),
            (b"connection", b"close"),
        ],
    })
    await send({"type": "http.response.body", "body": _TOO_LARGE_BODY})


class BodyLimitMiddleware:
    def __init__(self, app, max_bytes: int = MAX_BODY_BYTES) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        for key, value in scope.get("headers", ()):
            if key.lower() == b"content-length" and value.isdigit() and int(value) > self.max_bytes:
                await _reject(send)
                return

        received = 0
        response_started = False

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message.get("type") == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise _BodyTooLarge()
            return message

        async def tracking_send(message):
            nonlocal response_started
            if message.get("type") == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except _BodyTooLarge:
            if not response_started:
                await _reject(send)
