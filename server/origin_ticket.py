"""Origin-v1 ticket verification shared by the Bridge and kore-server.

The ticket format is ``base64url(canonical-json).base64url(hmac-sha256)``.
Only the private CoachBridge signs tickets. The server verifies them here; nonce
consumption belongs in the same SQLite transaction as the plan insert.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any, Mapping, Optional


_CLAIM_KEYS = {
    "version",
    "conversation_id",
    "principal",
    "nonce",
    "issued_at",
    "expires_at",
}
_NONCE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{22,128}$")
_PRINCIPAL_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._~-]{0,127}$")
_MAX_TICKET_LENGTH = 4096
_HARD_MAX_TTL_SECONDS = 300
_HARD_MAX_CLOCK_SKEW_SECONDS = 60


class InvalidOriginTicket(ValueError):
    """The ticket is unauthentic, malformed, or outside its validity window."""


@dataclass(frozen=True)
class OriginTicketPolicy:
    principal: str
    max_ttl_seconds: int = 300
    clock_skew_seconds: int = 5

    def __post_init__(self) -> None:
        if not _PRINCIPAL_PATTERN.fullmatch(self.principal):
            raise ValueError("origin principal has an invalid format")
        if (
            type(self.max_ttl_seconds) is not int
            or not 0 < self.max_ttl_seconds <= _HARD_MAX_TTL_SECONDS
        ):
            raise ValueError("max TTL must be an integer from 1 through 300")
        if (
            type(self.clock_skew_seconds) is not int
            or not 0 <= self.clock_skew_seconds <= _HARD_MAX_CLOCK_SKEW_SECONDS
        ):
            raise ValueError("clock skew must be an integer from 0 through 60")


@dataclass(frozen=True)
class VerifiedOrigin:
    version: int
    conversation_id: str
    principal: str
    nonce: str
    issued_at: int
    expires_at: int


def secret_from_env(name: str = "KORE_PLAN_ORIGIN_SECRET") -> bytes:
    value = os.environ.get(name)
    if value is None:
        raise RuntimeError(f"required environment variable {name} is missing")
    secret = value.encode("utf-8")
    _validate_secret(secret)
    return secret


def policy_from_env() -> OriginTicketPolicy:
    principal = os.environ.get("KORE_PLAN_ORIGIN_PRINCIPAL")
    if principal is None:
        raise RuntimeError(
            "required environment variable KORE_PLAN_ORIGIN_PRINCIPAL is missing"
        )
    try:
        max_ttl = int(os.environ.get("KORE_PLAN_ORIGIN_MAX_TTL_SECONDS", "300"))
        clock_skew = int(
            os.environ.get("KORE_PLAN_ORIGIN_CLOCK_SKEW_SECONDS", "5")
        )
    except ValueError as error:
        raise RuntimeError("origin TTL and clock skew must be integers") from error
    return OriginTicketPolicy(
        principal=principal,
        max_ttl_seconds=max_ttl,
        clock_skew_seconds=clock_skew,
    )


def issue_origin_ticket(
    *,
    secret: bytes,
    conversation_id: str,
    principal: str,
    nonce: str,
    issued_at: int,
    expires_at: int,
) -> str:
    """Create the canonical ticket that CoachBridge must attach to its tool call."""
    _validate_secret(secret)
    claims = {
        "version": 1,
        "conversation_id": conversation_id,
        "principal": principal,
        "nonce": nonce,
        "issued_at": issued_at,
        "expires_at": expires_at,
    }
    origin = _parse_claims(claims)
    if origin.expires_at <= origin.issued_at:
        raise ValueError("origin ticket expiry must be after issue time")
    encoded_payload = _encode_payload(claims)
    signature = hmac.new(secret, encoded_payload.encode("ascii"), hashlib.sha256)
    return f"{encoded_payload}.{_b64url_encode(signature.digest())}"


def verify_origin_ticket(
    ticket: str,
    *,
    secret: bytes,
    policy: OriginTicketPolicy,
    now: Optional[int] = None,
) -> VerifiedOrigin:
    _validate_secret(secret)
    if not isinstance(ticket, str) or not ticket or len(ticket) > _MAX_TICKET_LENGTH:
        raise InvalidOriginTicket("origin ticket has an invalid envelope")
    parts = ticket.split(".")
    if len(parts) != 2 or not all(parts):
        raise InvalidOriginTicket("origin ticket has an invalid envelope")
    encoded_payload, encoded_signature = parts
    try:
        supplied_signature = _b64url_decode(encoded_signature)
    except (ValueError, UnicodeError) as error:
        raise InvalidOriginTicket("origin ticket signature is malformed") from error
    try:
        signing_input = encoded_payload.encode("ascii")
    except UnicodeError as error:
        raise InvalidOriginTicket("origin ticket payload is malformed") from error
    expected_signature = hmac.new(secret, signing_input, hashlib.sha256).digest()
    if not hmac.compare_digest(supplied_signature, expected_signature):
        raise InvalidOriginTicket("origin ticket signature is invalid")

    try:
        payload_bytes = _b64url_decode(encoded_payload)
        claims = json.loads(payload_bytes.decode("utf-8"), object_pairs_hook=_unique_object)
    except (UnicodeError, ValueError, TypeError, json.JSONDecodeError) as error:
        raise InvalidOriginTicket("origin ticket payload is malformed") from error
    if not isinstance(claims, dict):
        raise InvalidOriginTicket("origin ticket payload must be an object")
    if _encode_payload(claims) != encoded_payload:
        raise InvalidOriginTicket("origin ticket payload is not canonical")

    origin = _parse_claims(claims)
    if origin.principal != policy.principal:
        raise InvalidOriginTicket("origin ticket principal does not match this instance")

    current_time = int(time.time()) if now is None else now
    if type(current_time) is not int:
        raise ValueError("current time must be an integer Unix timestamp")
    ttl = origin.expires_at - origin.issued_at
    if ttl <= 0 or ttl > policy.max_ttl_seconds:
        raise InvalidOriginTicket("origin ticket TTL is invalid")
    if origin.issued_at > current_time + policy.clock_skew_seconds:
        raise InvalidOriginTicket("origin ticket was issued in the future")
    if origin.expires_at < current_time - policy.clock_skew_seconds:
        raise InvalidOriginTicket("origin ticket has expired")
    return origin


def _parse_claims(claims: Mapping[str, Any]) -> VerifiedOrigin:
    if set(claims) != _CLAIM_KEYS:
        raise InvalidOriginTicket("origin ticket must contain exactly the v1 fields")
    if type(claims["version"]) is not int or claims["version"] != 1:
        raise InvalidOriginTicket("origin ticket version must be integer 1")
    for key in ("conversation_id", "principal", "nonce"):
        if type(claims[key]) is not str:
            raise InvalidOriginTicket(f"origin ticket {key} must be a string")
    for key in ("issued_at", "expires_at"):
        if type(claims[key]) is not int:
            raise InvalidOriginTicket(f"origin ticket {key} must be an integer")

    conversation_id = claims["conversation_id"]
    try:
        parsed_conversation_id = uuid.UUID(conversation_id)
    except (ValueError, AttributeError) as error:
        raise InvalidOriginTicket("conversation_id must be a UUID") from error
    if (
        str(parsed_conversation_id) != conversation_id
        or parsed_conversation_id.variant != uuid.RFC_4122
    ):
        raise InvalidOriginTicket(
            "conversation_id must be a canonical lowercase RFC4122 UUID"
        )
    principal = claims["principal"]
    if not _PRINCIPAL_PATTERN.fullmatch(principal):
        raise InvalidOriginTicket("origin ticket principal is invalid")
    nonce = claims["nonce"]
    if not _NONCE_PATTERN.fullmatch(nonce):
        raise InvalidOriginTicket("origin ticket nonce is invalid")

    return VerifiedOrigin(
        version=1,
        conversation_id=conversation_id,
        principal=principal,
        nonce=nonce,
        issued_at=claims["issued_at"],
        expires_at=claims["expires_at"],
    )


def _validate_secret(secret: bytes) -> None:
    if not isinstance(secret, bytes) or len(secret) < 32:
        raise ValueError("origin HMAC secret must contain at least 32 bytes")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _encode_payload(claims: Mapping[str, Any]) -> str:
    payload = json.dumps(
        claims,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return _b64url_encode(payload)


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    if not value or "=" in value:
        raise ValueError("base64url value must be unpadded")
    padding = "=" * (-len(value) % 4)
    return base64.b64decode(
        (value + padding).encode("ascii"),
        altchars=b"-_",
        validate=True,
    )
