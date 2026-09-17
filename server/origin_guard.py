"""Origin guard — the DNS-rebinding protection FastMCP 3.4 does not wire up.

Both surfaces of this repo speak plain HTTP on a private address. A browser that a
malicious page controls can therefore reach them: the page cannot read a cross-origin
response without CORS, but it can *send* requests, and a DNS-rebinding trick makes even
the read side possible. The MCP SDK ships `TransportSecuritySettings` for exactly this,
but FastMCP 3.4 never passes them through, and their default is
`enable_dns_rebinding_protection=False`. So the check lives here instead.

Policy, deliberately strict and deliberately small:

* **No `Origin` header → pass.** That is every non-browser client: the iOS app, the
  hermes CLI, `curl`, the coach bridge. They are the only intended callers.
* **`Origin` present and listed in `KORE_MCP_ALLOWED_ORIGINS` → pass.**
* **Anything else → 403.** Including an empty or repeated `Origin` header.

The default allowlist is empty, which means "no browser at all". Someone who really
wants a browser client puts their exact origin into the variable; nothing about that is
guessed for them.

This is not a replacement for the bearer token — it is the second lock on the same door.
"""

from __future__ import annotations

import os
from collections.abc import Iterable

ALLOWED_ORIGINS_ENV = "KORE_MCP_ALLOWED_ORIGINS"

_FORBIDDEN_BODY = b'{"detail":"Forbidden origin"}'


def normalize_origin(value: str) -> str:
    """Case-fold scheme and host and drop one trailing slash.

    An origin is `scheme://host[:port]`; RFC 6454 serializes it lowercase and without a
    path, but configuration is written by hand, so both are tolerated on the way in.
    """
    return value.strip().rstrip("/").lower()


def allowed_origins_from_env(environ=None) -> frozenset[str]:
    """Read the comma-separated allowlist. Absent or empty means: no browser origin."""
    source = os.environ if environ is None else environ
    raw = source.get(ALLOWED_ORIGINS_ENV, "")
    return frozenset(
        normalized
        for entry in raw.split(",")
        if (normalized := normalize_origin(entry))
    )


def origin_is_allowed(origin, allowed: Iterable[str]) -> bool:
    """`origin` is the raw header value, or None when the header was absent."""
    if origin is None:
        return True
    return normalize_origin(origin) in frozenset(allowed)


class OriginGuardMiddleware:
    """Pure-ASGI so the same class can sit in front of FastAPI and of FastMCP.

    Neither framework's own middleware helpers are used: the FastMCP app is assembled
    inside `run()`, and a plain ASGI callable is the one shape both accept.
    """

    def __init__(self, app, allowed_origins: Iterable[str] | None = None) -> None:
        self.app = app
        self.allowed_origins = frozenset(
            allowed_origins if allowed_origins is not None else allowed_origins_from_env()
        )

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        origins = [
            value.decode("latin-1")
            for key, value in scope.get("headers", ())
            if key == b"origin"
        ]
        # Two Origin headers are not a browser Kore should second-guess; they are a
        # smuggling attempt or a broken proxy. Either way the request is ambiguous.
        allowed = len(origins) <= 1 and origin_is_allowed(
            origins[0] if origins else None,
            self.allowed_origins,
        )
        if allowed:
            await self.app(scope, receive, send)
            return
        await send(
            {
                "type": "http.response.start",
                "status": 403,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(_FORBIDDEN_BODY)).encode("ascii")),
                ],
            }
        )
        # The rejected origin is never echoed back: that would hand a probing page a
        # confirmation channel, and it is already in the caller's own request.
        await send({"type": "http.response.body", "body": _FORBIDDEN_BODY})
