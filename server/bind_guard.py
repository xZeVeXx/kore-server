"""Wo Ingest und MCP lauschen duerfen: Loopback, RFC 1918, 100.64.0.0/10 (VPN) -- nie Wildcard.

Dieselbe Regel wie bei der Coach-Bridge (`validated_bind_host`): eine konkrete IPv4-Adresse, kein
Hostname, kein `0.0.0.0`/`::`. Ohne Docker ist die Lausch-Adresse die Angriffsflaeche und wird
geprueft (systemd: `KORE_BIND_ADDRESS`, `KORE_MCP_HOST`).

Im Container lauscht der Prozess zwangslaeufig auf 0.0.0.0 -- Dockers Portweiterleitung erreicht
ihn nur dort. Dort entscheidet, auf welcher Host-Adresse Compose den Port veroeffentlicht. Compose
reicht diese Adresse als `KORE_PUBLISHED_BIND` in den Container; der Prozess prueft sie nach
derselben Regel und lauscht nur dann auf 0.0.0.0. Compose selbst kann den Wert nicht pruefen, der
Container verweigert deshalb den Start. Die Variable gehoert in die Compose-Datei, nicht in die `.env`.
"""

from __future__ import annotations

import ipaddress
import os
from collections.abc import Mapping

PUBLISHED_BIND_ENV = "KORE_PUBLISHED_BIND"
LOOPBACK = "127.0.0.1"
_WILDCARDS = ("0.0.0.0", "::")
_ALLOWED_NETWORKS = tuple(ipaddress.ip_network(network) for network in (
    "127.0.0.0/8",
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "100.64.0.0/10",  # CGNAT range used by Tailscale and similar VPNs
))


def _refusal(name: str) -> RuntimeError:
    return RuntimeError(
        f"{name} must be a concrete IPv4 loopback, RFC 1918 or VPN (100.64.0.0/10) address "
        "(no wildcard, no hostname)"
    )


def validated_bind_host(value: object, name: str) -> str:
    """The address itself, or a RuntimeError naming `name` -- never a wildcard or a public address."""
    if not isinstance(value, str) or value != value.strip():
        raise _refusal(name)
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        raise _refusal(name) from None
    if not isinstance(address, ipaddress.IPv4Address) or not any(
        address in network for network in _ALLOWED_NETWORKS
    ):
        raise _refusal(name)
    return str(address)


def listen_host(
    name: str,
    environ: Mapping[str, str] | None = None,
    *,
    fallback: str | None = None,
) -> str:
    """The host a service may listen on, read from `name` (then `fallback`; default loopback).

    An unset or empty value means loopback, matching Compose's `${VAR:-127.0.0.1}`.
    """
    source = os.environ if environ is None else environ
    key, value = name, source.get(name) or None
    if value is None and fallback is not None:
        key, value = fallback, source.get(fallback) or None
    if value is None:
        key, value = name, LOOPBACK
    published = source.get(PUBLISHED_BIND_ENV)
    if published is None:
        if value in _WILDCARDS:
            raise RuntimeError(
                f"{key} must not be a wildcard address. Only the Docker image listens on every "
                f"interface, and Compose sets {PUBLISHED_BIND_ENV} there to the host address the "
                "port is published on."
            )
        return validated_bind_host(value, key)
    # Inside the container: the exposure is the host address Compose publishes the port on.
    validated_bind_host(
        published,
        f"{PUBLISHED_BIND_ENV} (the host address Compose publishes the port on, KORE_BIND_ADDRESS in .env)",
    )
    if value == "0.0.0.0":
        return value
    return validated_bind_host(value, key)
