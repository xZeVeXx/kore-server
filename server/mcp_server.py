"""Private FastMCP facade for one Kore database and one configured principal."""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
from typing import Any


_SAFE_FASTMCP_LOG_LEVELS = {
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}


def _required_fastmcp_log_level() -> tuple[str, int]:
    """Reject an explicit FastMCP level that could serialize private tool input."""
    if "FASTMCP_LOG_LEVEL" not in os.environ:
        return "ERROR", logging.ERROR
    raw_level = os.environ["FASTMCP_LOG_LEVEL"]
    normalized = raw_level.upper()
    numeric_level = _SAFE_FASTMCP_LOG_LEVELS.get(normalized)
    if numeric_level is None:
        raise RuntimeError("FASTMCP_LOG_LEVEL must be ERROR or CRITICAL")
    return normalized, numeric_level


# This gate must run before importing FastMCP. Integration processes may already
# have imported it, so the programmatic clamp immediately below is also required.
_FASTMCP_LOG_LEVEL, _FASTMCP_LOG_LEVEL_NUMBER = _required_fastmcp_log_level()
# FastMCP 3.4 asks PyPI for a newer release when it prints its start banner (settings
# `check_for_updates`, env FASTMCP_CHECK_FOR_UPDATES, default "stable"). The server makes no
# outbound call: off before the import, and again on the settings object below.
os.environ["FASTMCP_CHECK_FOR_UPDATES"] = "off"

import fastmcp  # noqa: E402
from fastmcp import FastMCP
from fastmcp.server.auth import AccessToken, TokenVerifier
from fastmcp.utilities.logging import configure_logging
from starlette.middleware import Middleware

import store
from auth_gate import SingleAuthorizationMiddleware
from bind_guard import UVICORN_LIMITS, listen_host
from models import TrainingPlanProposal
from origin_guard import OriginGuardMiddleware, allowed_origins_from_env
from origin_ticket import policy_from_env, secret_from_env, verify_origin_ticket
from placeholder_guard import MIN_TOKEN_LENGTH, reject_placeholder


def _clamp_logger_namespace(namespace: str, minimum_level: int) -> None:
    """Clamp loaded and future loggers in a private protocol namespace."""
    namespace_logger = logging.getLogger(namespace)
    namespace_logger.setLevel(minimum_level)
    for handler in namespace_logger.handlers:
        handler.setLevel(minimum_level)
    for name, candidate in logging.Logger.manager.loggerDict.items():
        if (
            isinstance(candidate, logging.Logger)
            and name.startswith(f"{namespace}.")
        ):
            candidate.setLevel(minimum_level)
            for handler in candidate.handlers:
                handler.setLevel(minimum_level)


def _clamp_root_logger(minimum_level: int) -> None:
    # MCP's request-validation failure path uses module-level logging.warning()
    # and logging.debug(), which bypass named mcp/fastmcp logger levels.
    root_logger = logging.getLogger()
    root_logger.setLevel(minimum_level)
    for handler in root_logger.handlers[:]:
        # The bundled legacy mcp.server.fastmcp may already have installed this
        # on root with rich_tracebacks=True before the candidate is imported.
        if (
            handler.__class__.__module__ == "rich.logging"
            and handler.__class__.__name__ == "RichHandler"
        ):
            root_logger.removeHandler(handler)
            replacement = logging.StreamHandler()
            replacement.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
            replacement.setLevel(minimum_level)
            root_logger.addHandler(replacement)
        else:
            handler.setLevel(minimum_level)


def _harden_private_protocol_logging() -> None:
    # FastMCP configures logging during import. Reset both its mutable settings
    # and existing handlers so a prior import cannot retain DEBUG/WARNING output.
    fastmcp.settings.log_enabled = True
    fastmcp.settings.log_level = _FASTMCP_LOG_LEVEL
    fastmcp.settings.enable_rich_tracebacks = False
    fastmcp.settings.enable_rich_logging = False
    fastmcp.settings.debug = False
    configure_logging(
        level=_FASTMCP_LOG_LEVEL,
        enable_rich_tracebacks=False,
    )
    _clamp_root_logger(_FASTMCP_LOG_LEVEL_NUMBER)
    _clamp_logger_namespace("fastmcp", _FASTMCP_LOG_LEVEL_NUMBER)
    # The bundled MCP SDK also has DEBUG paths that serialize complete requests.
    _clamp_logger_namespace("mcp", _FASTMCP_LOG_LEVEL_NUMBER)


_harden_private_protocol_logging()
fastmcp.settings.check_for_updates = "off"


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"required environment variable {name} is missing")
    return value


def _required_secret(name: str, *, min_length: int = 0) -> str:
    value = os.environ.get(name)
    reject_placeholder(name, value, min_length=min_length)
    return value


class ConstantTimeTokenVerifier(TokenVerifier):
    """One configured bearer token, compared in constant time.

    FastMCP's `StaticTokenVerifier` looks the presented token up in a dict and says itself:
    "Never use this in production". Here both sides are hashed to equal length and compared with
    `hmac.compare_digest`, so neither the content nor the length of the token shows in the timing.
    """

    def __init__(
        self,
        token: str,
        client_id: str,
        scopes: list[str],
        required_scopes: list[str] | None = None,
    ) -> None:
        super().__init__(required_scopes=required_scopes)
        self._expected_digest = self._digest(token)
        self._client_id = client_id
        self._scopes = list(scopes)

    @staticmethod
    def _digest(token: str) -> bytes:
        # surrogatepass: a header value is never a reason to raise instead of refusing.
        return hashlib.sha256(token.encode("utf-8", "surrogatepass")).digest()

    async def verify_token(self, token: str) -> AccessToken | None:
        if not hmac.compare_digest(self._digest(token), self._expected_digest):
            return None
        return AccessToken(
            token=token,
            client_id=self._client_id,
            scopes=list(self._scopes),
            expires_at=None,
        )


MCP_TOKEN = _required_secret("KORE_MCP_TOKEN", min_length=MIN_TOKEN_LENGTH)
MCP_CLIENT_ID = _required_env("KORE_MCP_CLIENT_ID")
_required_secret("KORE_PLAN_ORIGIN_SECRET")
ORIGIN_SECRET = secret_from_env()
ORIGIN_POLICY = policy_from_env()

token_verifier = ConstantTimeTokenVerifier(
    MCP_TOKEN,
    client_id=MCP_CLIENT_ID,
    scopes=["read:health", "write:plan"],
    required_scopes=["read:health"],
)
mcp = FastMCP("Kore", auth=token_verifier)
ALLOWED_ORIGINS = allowed_origins_from_env()
store.init()


def http_middleware() -> list[Middleware]:
    """The ASGI layers FastMCP mounts outside its own session handling: the origin guard, then
    the refusal of a second `Authorization` header (401 before the body, as on ingest and bridge).

    FastMCP 3.4 builds its Starlette app inside `run()`, so the guards are handed in as
    middleware specs rather than wrapped around a finished app. `TransportSecuritySettings`
    from the MCP SDK would be the built-in route for the origin check, but FastMCP never forwards it.
    """
    return [
        Middleware(OriginGuardMiddleware, allowed_origins=ALLOWED_ORIGINS),
        Middleware(SingleAuthorizationMiddleware),
    ]


def _snapshot_freshness(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Expose source and receipt time so callers can reject stale read-side truth."""
    return {
        "snapshot_generated_at": snapshot.get("generated_at"),
        "snapshot_received_at": snapshot.get("_updated_at"),
    }


@mcp.tool
def get_recovery_today() -> dict[str, Any]:
    """Heutige Readiness/HRV(Schlaf)/Ruhepuls/Schlaf-Zusammenfassung aus dem letzten App-Snapshot.

    hrv_kind appleRMSSD = Apples Erholungs-HRV: eigene Skala, nicht mit rMSSD/SDNN vergleichen."""
    return store.get_snapshot().get("recovery") or {
        "note": "noch keine Daten synchronisiert"
    }


@mcp.tool
def get_hrv_trend(days: int = 30) -> dict[str, list[dict[str, Any]]]:
    """HRV-Verlauf (SDNN, ms) der letzten Tage."""
    points = store.get_snapshot().get("hrv_trend") or []
    return {"values": points[-days:]}


@mcp.tool
def get_sleep_summary(days: int = 7) -> dict[str, list[dict[str, Any]]]:
    """Schlaf der letzten Nächte (Dauer, Tief, REM, Effizienz)."""
    nights = store.get_snapshot().get("sleep") or []
    return {"nights": nights[-days:]}


@mcp.tool
def get_recent_workouts(limit: int = 10) -> dict[str, list[dict[str, Any]]]:
    """Zuletzt aufgezeichnete/geloggte Workouts."""
    workouts = store.get_snapshot().get("workouts") or []
    return {"workouts": workouts[:limit]}


# Snapshot v2 (contract/snapshot-v2.md): daily lists of at most 31 entries.
TREND_MAX_DAYS = 30


def _recent_days(snapshot: dict[str, Any], key: str, days: int) -> list[dict[str, Any]]:
    """The last `days` entries (bounded to 1…30) of a daily list, newest last."""
    days = max(1, min(days, TREND_MAX_DAYS))
    entries = sorted(snapshot.get(key) or [], key=lambda entry: entry["date"])
    return entries[-days:]


@mcp.tool
def get_activity_body_trend(days: int = 30) -> dict[str, list[dict[str, Any]]]:
    """Schritte (count, Tagessumme) und Körper (weight_kg in kg, body_fat_pct in %, letzter Wert
    des Tages) aus Apple Health: die letzten `days` Tage mit Werten (1–30), neueste zuletzt."""
    snapshot = store.get_snapshot()
    return {
        "steps": _recent_days(snapshot, "steps", days),
        "body": _recent_days(snapshot, "body", days),
    }


@mcp.tool
def get_nutrition_trend(days: int = 30) -> dict[str, list[dict[str, Any]]]:
    """Ernährung je Tag: Energie (kcal), Eiweiß (protein_g), Kohlenhydrate (carbs_g) und Fett
    (fat_g) in g. Tagessummen aus Apple Health — unvollständig, wenn nicht alles erfasst wird.
    Die letzten `days` Tage mit Werten (1–30), neueste zuletzt; leer, wenn nichts geteilt wird."""
    return {"nutrition": _recent_days(store.get_snapshot(), "nutrition", days)}


@mcp.tool
def get_active_plan() -> dict[str, Any]:
    """Name des aktuell aktiven Trainingsplans (falls vorhanden)."""
    snapshot = store.get_snapshot()
    return {
        "active_plan_name": snapshot.get("active_plan_name"),
        "profile": snapshot.get("profile"),
    }


@mcp.tool
def get_training_plans() -> dict[str, Any]:
    """Alle Trainingspläne des Nutzers in voller Struktur (Name, aktiv, Sessions, Übungen, Sätze).
    Nur read-only Orientierung: Dieser Snapshot darf selbst mit Zeitstempeln niemals Grundlage
    eines Planvorschlags sein. Eine lokale plan_id als replaces_plan_id darf ausschließlich aus
    einem frischen vollständigen App-Kontext mit plans_scope.complete=true stammen."""
    snapshot = store.get_snapshot()
    return {
        "plans": snapshot.get("plans") or [],
        **_snapshot_freshness(snapshot),
    }


@mcp.tool
def get_coach_briefing() -> dict[str, Any]:
    """Das komplette Tagesbild in EINEM Call: Recovery heute, HRV-Trend (7 Tage), Schlaf (7 Nächte),
    letzte Workouts, Profil und alle Trainingspläne; dazu Schritte/Gewicht (activity_body_7d) und
    Ernährung (nutrition_7d), je die letzten 7 Tage mit Werten (Einheiten wie in
    get_activity_body_trend/get_nutrition_trend). Bevorzugt gegenüber Einzeltools (ein Roundtrip).
    Hinweis: Chat-Nachrichten aus der Kore-App enthalten dasselbe Briefing bereits als Kontext."""
    snapshot = store.get_snapshot()
    return {
        "recovery_today": snapshot.get("recovery"),
        "hrv_trend": (snapshot.get("hrv_trend") or [])[-7:],
        "sleep": (snapshot.get("sleep") or [])[-7:],
        "recent_workouts": (snapshot.get("workouts") or [])[:8],
        "profile": snapshot.get("profile"),
        "active_plan_name": snapshot.get("active_plan_name"),
        "plans": snapshot.get("plans") or [],
        "activity_body_7d": {
            "steps": _recent_days(snapshot, "steps", 7),
            "body": _recent_days(snapshot, "body", 7),
        },
        "nutrition_7d": _recent_days(snapshot, "nutrition", 7),
        **_snapshot_freshness(snapshot),
    }


@mcp.tool
def propose_training_plan(
    plan: TrainingPlanProposal,
    origin_ticket: str,
) -> dict[str, str]:
    """Schlägt der App einen Trainingsplan vor (Status 'pending' → Nutzer bestätigt in der App).
    Updates brauchen Ziel-ID, frischen App-Kontext-Fingerprint und beide exakten Removal-Arrays;
    diese Werte dürfen nie aus den read-only MCP-Tools stammen. Neue Pläne lassen sie weg.
    Erfordert ein authentifiziertes, kurzlebiges Einmal-Ticket der privaten CoachBridge."""
    origin = verify_origin_ticket(
        origin_ticket,
        secret=ORIGIN_SECRET,
        policy=ORIGIN_POLICY,
    )
    plan_id = store.add_plan(plan.model_dump(exclude_none=True), origin)
    return {"plan_id": plan_id, "status": "pending"}


def serve() -> None:
    mcp.run(
        transport="http",
        host=listen_host("KORE_MCP_HOST"),
        port=int(os.environ.get("KORE_MCP_PORT", "8000")),
        middleware=http_middleware(),
        uvicorn_config=dict(UVICORN_LIMITS),
    )


if __name__ == "__main__":
    serve()
