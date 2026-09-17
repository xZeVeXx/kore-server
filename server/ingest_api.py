"""Private FastAPI ingest and pending-plan surface for one Kore principal."""

from __future__ import annotations

import hmac
import os

from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError

import store
from models import PlanAck, Snapshot
from origin_ticket import policy_from_env


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"required environment variable {name} is missing")
    return value


INGEST_TOKEN = _required_env("KORE_INGEST_TOKEN")
ORIGIN_POLICY = policy_from_env()

app = FastAPI(title="Kore Ingest API", version="1.0")


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
    expected = f"Bearer {INGEST_TOKEN}"
    if not hmac.compare_digest(authorization, expected):
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
