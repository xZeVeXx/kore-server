"""Startsperre fuer Geheimnisse, die leer, zu kurz sind oder noch aus `.env.example` stammen."""

from __future__ import annotations

PLACEHOLDER_PREFIX = "change-me"
# Bearer-Tokens: `openssl rand -hex 24` ergibt 48 Zeichen; unter 32 ist ein Token erratbar genug,
# dass ein Fremder im LAN es durchprobieren koennte.
MIN_TOKEN_LENGTH = 32


def reject_placeholder(name: str, value: str | None, *, min_length: int = 0) -> None:
    """Bricht den Start ab. Die Meldung nennt nur den Schluessel, nie den Wert."""
    if value is None or not value.strip():
        raise RuntimeError(f"required environment variable {name} is missing or empty")
    if value.strip().lower().startswith(PLACEHOLDER_PREFIX):
        raise RuntimeError(
            f"{name} still holds the example value from .env.example; "
            "generate a real one (openssl rand -hex 32) before starting"
        )
    if len(value.strip()) < min_length:
        raise RuntimeError(
            f"{name} is too short: at least {min_length} characters are required; "
            "generate one with openssl rand -hex 24"
        )
