"""Startsperre fuer Geheimnisse, die leer sind oder noch aus `.env.example` stammen."""

from __future__ import annotations

PLACEHOLDER_PREFIX = "change-me"


def reject_placeholder(name: str, value: str | None) -> None:
    """Bricht den Start ab. Die Meldung nennt nur den Schluessel, nie den Wert."""
    if value is None or not value.strip():
        raise RuntimeError(f"required environment variable {name} is missing or empty")
    if value.strip().lower().startswith(PLACEHOLDER_PREFIX):
        raise RuntimeError(
            f"{name} still holds the example value from .env.example; "
            "generate a real one (openssl rand -hex 32) before starting"
        )
