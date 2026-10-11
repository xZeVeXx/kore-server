"""Bearer check in the ASGI layer, before anything reads the request body.

FastAPI reads and JSON-parses a request body (up to 16 MiB here) before it resolves the bearer
dependency of a route. One 15.7 MiB request without a token therefore cost ~400 MiB RSS and
blocked the event loop while `json.loads` ran, only to be answered with 401. This layer answers
401 for a protected route *without calling `receive` at all*, so an unauthenticated sender costs
a header comparison, not a parse.

Default deny: only the exact paths in `anonymous_paths` (`/health` for the ingest API) skip the
gate, and they get a body ceiling of a few KiB: nothing unauthenticated ever needs more. Every
other path, unknown ones included, needs the token before a single body byte is read. A list of
protected paths would miss variants the router still matches: Starlette's route regex ends in `$`,
which also matches before a final newline, so `/ingest%0A` reached the ingest route.

The route-level dependency in `ingest_api` stays as a second lock on the same door; both call
the same `authorize` function.
"""

from __future__ import annotations

from collections.abc import Callable, Collection

from body_limit import BodyLimitMiddleware

# The anonymous paths read no body at all; this only bounds what a sender may push at them.
ANONYMOUS_MAX_BODY_BYTES = 4 * 1024

_UNAUTHORIZED_BODY = b'{"detail":"Unauthorized"}'


async def _reject(send) -> None:
    await send({
        "type": "http.response.start",
        "status": 401,
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(_UNAUTHORIZED_BODY)).encode("ascii")),
            (b"www-authenticate", b"Bearer"),
            # The body was not read; closing makes the sender stop streaming it.
            (b"connection", b"close"),
        ],
    })
    await send({"type": "http.response.body", "body": _UNAUTHORIZED_BODY})


class BearerGateMiddleware:
    """Pure ASGI, like the origin guard and the body limit around it.

    `authorize(value)` gets the raw `Authorization` header value (latin-1, as the framework
    decodes headers) and must compare in constant time. `anonymous_paths` are the exact paths
    that need no token; everything else does. Exactly one `Authorization` header counts: a
    duplicate is ambiguous, not "the first one wins".
    """

    def __init__(
        self,
        app,
        authorize: Callable[[str], bool],
        anonymous_paths: Collection[str] = frozenset(),
        anonymous_max_bytes: int = ANONYMOUS_MAX_BODY_BYTES,
    ) -> None:
        self.app = app
        self.authorize = authorize
        self.anonymous_paths = frozenset(anonymous_paths)
        self.anonymous_app = BodyLimitMiddleware(app, max_bytes=anonymous_max_bytes)

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        if scope.get("path") in self.anonymous_paths:
            await self.anonymous_app(scope, receive, send)
            return
        supplied = [
            value for key, value in scope.get("headers", ()) if key.lower() == b"authorization"
        ]
        if len(supplied) == 1 and self.authorize(supplied[0].decode("latin-1")):
            await self.app(scope, receive, send)
            return
        await _reject(send)


class SingleAuthorizationMiddleware:
    """401 for a request with more than one `Authorization` header, before any of its body is read.

    For the MCP server: FastMCP checks the bearer token itself, but its backend reads only the first
    header. Two are ambiguous, as on the ingest API and the bridge.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") == "http" and sum(
            1 for key, _ in scope.get("headers", ()) if key.lower() == b"authorization"
        ) > 1:
            await _reject(send)
            return
        await self.app(scope, receive, send)
