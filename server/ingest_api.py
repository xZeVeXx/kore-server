"""Private FastAPI ingest and pending-plan surface for one Kore principal."""

from __future__ import annotations

import hmac
import os
import re

from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError

import auth_devices
import store
from auth_gate import BearerGateMiddleware
from bind_guard import listen_host
from body_limit import BodyLimitMiddleware
from models import PlanAck, Snapshot
from origin_guard import OriginGuardMiddleware, allowed_origins_from_env
from origin_ticket import policy_from_env
from placeholder_guard import MIN_TOKEN_LENGTH, reject_placeholder

PORT = 8081


def _required_secret(name: str) -> str:
    value = os.environ.get(name)
    reject_placeholder(name, value, min_length=MIN_TOKEN_LENGTH)
    return value


INGEST_TOKEN = _required_secret("KORE_INGEST_TOKEN")
# Paired devices (contract/pairing-v1.md), read-only; None without KORE_STATE_DIR.
DEVICE_TOKENS = auth_devices.from_env()
ORIGIN_POLICY = policy_from_env()

ALLOWED_ORIGINS = allowed_origins_from_env()


def authorization_ok(authorization: str) -> bool:
    """The static ingest token or a paired device's token; both checks always run."""
    # Bytes, not str: compare_digest raises on non-ASCII str, and headers arrive as latin-1.
    supplied = authorization.encode("utf-8", "surrogatepass")
    expected = f"Bearer {INGEST_TOKEN}".encode("utf-8")
    static_ok = hmac.compare_digest(supplied, expected)
    device_ok = DEVICE_TOKENS is not None and DEVICE_TOKENS.device_for_authorization(authorization) is not None
    return static_ok or device_ok


# Every route that requires the bearer token. Trailing slash included: FastAPI would redirect it.
_PROTECTED_PATH = re.compile(r"/(?:ingest|plan/pending|plan/[^/]+/ack)/?")


def is_protected_path(path: str) -> bool:
    return _PROTECTED_PATH.fullmatch(path) is not None


# No /docs, /redoc or /openapi.json: they would hand every unauthenticated caller the full schema.
app = FastAPI(
    title="Kore Ingest API", version="1.0",
    docs_url=None, redoc_url=None, openapi_url=None,
)
# Innermost: caps an authenticated body before FastAPI buffers it for parsing.
app.add_middleware(BodyLimitMiddleware)
# Between the body limit and the origin guard: a request without a valid token is answered 401
# before FastAPI reads or parses a single byte of its (up to 16 MiB) body.
app.add_middleware(BearerGateMiddleware, authorize=authorization_ok, protected=is_protected_path)
# Outermost layer on purpose: a rejected origin must not reach routing, validation or the
# token check, so a probing page learns nothing from response shape or timing. /health is
# covered too — it is the cheapest existence oracle on the box.
app.add_middleware(OriginGuardMiddleware, allowed_origins=ALLOWED_ORIGINS)


@app.exception_handler(RequestValidationError)
async def private_validation_error_handler(
    _request: object,
    _error: RequestValidationError,
) -> JSONResponse:
    # Never reflect malformed Health/plan input. FastAPI's default detail payload can also contain
    # non-JSON values such as NaN, turning a correct validation failure into a response-time 500.
    return JSONResponse(status_code=422, content={"detail": "Invalid request"})


@app.on_event("startup")
def startup() -> None:
    store.init()


def require_ingest_token(authorization: str = Header(default="")) -> None:
    """Second lock behind the ASGI gate: the same check, should a route ever bypass the gate."""
    if not authorization_ok(authorization):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Bearer"},
        )


@app.get("/health")
def health() -> dict[str, bool]:
    return {"ok": True}


def _validate_snapshot_for_storage(payload: dict[str, object]) -> None:
    Snapshot.model_validate(payload)


@app.post("/ingest", dependencies=[Depends(require_ingest_token)])
def ingest(snapshot: Snapshot) -> dict[str, bool]:
    # Preserve the sender's field presence. In particular, never synthesize an HRV alias or
    # provenance value that the method-aware iOS projection did not send.
    payload = snapshot.model_dump(mode="json", exclude_unset=True)
    workouts_scope = payload.pop("workouts_scope", None)
    workout_update: store.WorkoutUpdateMode
    if workouts_scope == "unconfirmed":
        workout_update = "preserve"
    elif workouts_scope == "health_unconfirmed":
        workout_update = "replace_local"
    else:
        workout_update = "replace"
    try:
        store.save_snapshot(
            payload,
            workout_update=workout_update,
            validate_final_payload=_validate_snapshot_for_storage,
        )
    except ValidationError:
        # The merge can exceed a whole-snapshot invariant even though each side was valid.
        raise HTTPException(
            status_code=422,
            detail="Invalid request",
        ) from None
    return {"ok": True}


@app.get("/plan/pending", dependencies=[Depends(require_ingest_token)])
def pending_plans() -> dict[str, list[dict[str, object]]]:
    return {"plans": store.pending_plans(ORIGIN_POLICY.principal)}


@app.post("/plan/{plan_id}/ack", dependencies=[Depends(require_ingest_token)])
def ack_plan(plan_id: str, ack: PlanAck) -> dict[str, object]:
    acknowledged = store.ack_plan(
        plan_id,
        accepted=ack.accepted,
        principal=ORIGIN_POLICY.principal,
    )
    if not acknowledged:
        # Do not disclose whether another private instance owns this identifier.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return {"ok": True, "status": "accepted" if ack.accepted else "rejected"}


if __name__ == "__main__":
    import uvicorn

    # KORE_INGEST_HOST (Docker) before KORE_BIND_ADDRESS (systemd); loopback when neither is set.
    uvicorn.run(app, host=listen_host("KORE_INGEST_HOST", fallback="KORE_BIND_ADDRESS"), port=PORT)
