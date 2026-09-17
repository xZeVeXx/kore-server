"""Pydantic contracts mirrored from the original private kore-server."""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any, Literal, Optional

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictBool,
    model_validator,
)
from pydantic.json_schema import SkipJsonSchema

from plan_contract import (
    EXERCISE_NAME_BYTES,
    EXERCISE_NOTE_BYTES,
    EXERCISES_PER_SESSION,
    MAXIMUM_CARDIO_DISTANCE_KM,
    MAXIMUM_CARDIO_DURATION_MINUTES,
    MAXIMUM_REPETITIONS,
    MAXIMUM_REST_SECONDS,
    MAXIMUM_WARMUP_SETS,
    MAXIMUM_WARMUP_WEIGHT_KG,
    MAXIMUM_WEIGHT_KG,
    MINIMUM_CARDIO_DURATION_MINUTES,
    PLAN_NAME_BYTES,
    PLAN_FINGERPRINT_BYTES,
    RATIONALE_BYTES,
    REPLACEMENT_NAME_BYTES,
    SESSION_NAME_BYTES,
    SESSIONS_PER_PLAN,
    SETS_PER_EXERCISE,
    validate_and_normalize_plan,
)
from snapshot_contract import validate_recovery_hrv_contract


MAX_SNAPSHOT_ELEMENTS = 20_000
MAX_HRV_MILLISECONDS = 1_000.0


def _parse_iso_day(value: Any) -> date:
    if type(value) is not str:
        raise ValueError("date must be an ISO 8601 calendar-date string")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise ValueError("date must be a valid ISO 8601 calendar date") from error
    if parsed.isoformat() != value:
        raise ValueError("date must use canonical YYYY-MM-DD form")
    return parsed


def _parse_aware_iso_timestamp(value: Any) -> datetime:
    if type(value) is not str:
        raise ValueError("timestamp must be an ISO 8601 string with timezone")
    normalized = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as error:
        raise ValueError("timestamp must be a valid ISO 8601 value") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include an explicit timezone")
    return parsed


ISODate = Annotated[date, BeforeValidator(_parse_iso_day)]
ISODateTime = Annotated[datetime, BeforeValidator(_parse_aware_iso_timestamp)]
HRVMilliseconds = Annotated[
    float,
    Field(gt=0, le=MAX_HRV_MILLISECONDS, allow_inf_nan=False, strict=True),
]


class StrictSnapshotModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PlanSet(BaseModel):
    weight_kg: float = Field(
        default=0,
        ge=0,
        le=MAXIMUM_WEIGHT_KG,
        allow_inf_nan=False,
    )
    reps: int = Field(default=1, ge=1, le=MAXIMUM_REPETITIONS)
    reps_max: Annotated[
        int,
        Field(ge=1, le=MAXIMUM_REPETITIONS),
    ] | SkipJsonSchema[None] = None


class PlanExercise(BaseModel):
    exercise_id: Annotated[
        str,
        Field(max_length=36),
    ] | SkipJsonSchema[None] = None
    name: str = Field(min_length=1, max_length=EXERCISE_NAME_BYTES)
    note: Annotated[
        str,
        Field(max_length=EXERCISE_NOTE_BYTES),
    ] | SkipJsonSchema[None] = None
    sets: list[PlanSet] = Field(min_length=1, max_length=SETS_PER_EXERCISE)
    rest_seconds: Annotated[
        int,
        Field(ge=0, le=MAXIMUM_REST_SECONDS),
    ] | SkipJsonSchema[None] = None
    warmup_sets: Annotated[
        int,
        Field(ge=0, le=MAXIMUM_WARMUP_SETS),
    ] | SkipJsonSchema[None] = None
    warmup_weight_kg: Annotated[
        float,
        Field(ge=0, le=MAXIMUM_WARMUP_WEIGHT_KG, allow_inf_nan=False),
    ] | SkipJsonSchema[None] = None
    bilateral: bool | SkipJsonSchema[None] = None


class PlanCardioTarget(BaseModel):
    duration_min: Optional[int] = Field(
        default=None,
        ge=MINIMUM_CARDIO_DURATION_MINUTES,
        le=MAXIMUM_CARDIO_DURATION_MINUTES,
    )
    distance_km: Optional[float] = Field(
        default=None,
        gt=0,
        le=MAXIMUM_CARDIO_DISTANCE_KM,
        allow_inf_nan=False,
    )


class PlanSession(BaseModel):
    session_id: Optional[str] = Field(default=None, max_length=36)
    name: str = Field(min_length=1, max_length=SESSION_NAME_BYTES)
    kind: Optional[Literal["strength", "cardio"]] = None
    cardio_target: Optional[PlanCardioTarget] = None
    exercises: list[PlanExercise] = Field(
        default_factory=list,
        max_length=EXERCISES_PER_SESSION,
    )


class TrainingPlanProposal(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    name: str = Field(min_length=1, max_length=PLAN_NAME_BYTES)
    rationale: str = Field(default="", max_length=RATIONALE_BYTES)
    replaces_plan: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=REPLACEMENT_NAME_BYTES,
    )
    replaces_plan_id: Annotated[
        str,
        Field(max_length=36),
    ] | SkipJsonSchema[None] = None
    base_plan_fingerprint: Annotated[
        str,
        Field(min_length=PLAN_FINGERPRINT_BYTES, max_length=PLAN_FINGERPRINT_BYTES),
    ] | SkipJsonSchema[None] = None
    removed_session_ids: Annotated[
        list[Annotated[str, Field(max_length=36)]],
        Field(max_length=SESSIONS_PER_PLAN),
    ] | SkipJsonSchema[None] = None
    removed_exercise_ids: Annotated[
        list[Annotated[str, Field(max_length=36)]],
        Field(max_length=SESSIONS_PER_PLAN * EXERCISES_PER_SESSION),
    ] | SkipJsonSchema[None] = None
    sessions: list[PlanSession] = Field(
        min_length=1,
        max_length=SESSIONS_PER_PLAN,
    )

    @model_validator(mode="before")
    @classmethod
    def enforce_swift_output_contract(cls, value: Any) -> dict[str, Any]:
        return validate_and_normalize_plan(value)


class PlanAck(BaseModel):
    accepted: StrictBool


class RecoveryToday(StrictSnapshotModel):
    date: ISODate
    readiness: Optional[Annotated[int, Field(ge=0, le=100, strict=True)]] = None
    hrv_rmssd_ms: Optional[HRVMilliseconds] = None
    hrv_sdnn_ms: Optional[HRVMilliseconds] = None
    hrv_ms: Optional[HRVMilliseconds] = None
    hrv_baseline_ms: Optional[HRVMilliseconds] = None
    hrv_method_effective: Optional[
        Literal["rmssd", "sdnn", "sdnnFallbackFromRMSSD"]
    ] = None
    hrv_kind: Optional[Literal["rmssd", "sdnn"]] = None
    hrv_source: Optional[
        Literal[
            "nightHeartbeatSeriesRMSSD",
            "sleepHealthKitSDNN",
            "metric_tile_fallback",
        ]
    ] = None
    hrv_quality: Optional[
        Literal["good", "sparse", "fallback", "unavailable"]
    ] = None
    # Zero is meaningful for migrated local records whose historic sample count is unknown.
    hrv_sample_count: Optional[Annotated[int, Field(ge=0, strict=True)]] = None
    hrv_contributed_to_recovery: Optional[StrictBool] = None
    hrv_fallback_reason: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=2048,
    )
    resting_hr: Optional[Annotated[int, Field(ge=0, strict=True)]] = None
    sleep_h: Optional[
        Annotated[float, Field(ge=0, allow_inf_nan=False, strict=True)]
    ] = None
    band: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def enforce_method_safe_hrv_contract(cls, value: Any) -> Any:
        return validate_recovery_hrv_contract(value)


class HrvPoint(StrictSnapshotModel):
    date: ISODate
    sdnn_ms: HRVMilliseconds


class SleepNightDTO(StrictSnapshotModel):
    date: ISODate
    total_h: Annotated[
        float,
        Field(ge=0, allow_inf_nan=False, strict=True),
    ]
    deep_h: Optional[
        Annotated[float, Field(ge=0, allow_inf_nan=False, strict=True)]
    ] = None
    rem_h: Optional[
        Annotated[float, Field(ge=0, allow_inf_nan=False, strict=True)]
    ] = None
    efficiency_pct: Optional[
        Annotated[int, Field(ge=0, le=100, strict=True)]
    ] = None


class WorkoutDTO(StrictSnapshotModel):
    id: Optional[str] = None
    source: Optional[Literal["local", "health"]] = None
    date: ISODateTime
    type: str
    duration_min: Annotated[int, Field(ge=0, strict=True)]
    avg_hr: Optional[Annotated[int, Field(ge=0, strict=True)]] = None
    volume_kg: Optional[
        Annotated[float, Field(ge=0, allow_inf_nan=False, strict=True)]
    ] = None
    kcal: Optional[
        Annotated[float, Field(ge=0, allow_inf_nan=False, strict=True)]
    ] = None


class ProfileDTO(StrictSnapshotModel):
    age: Optional[Annotated[int, Field(ge=0, strict=True)]] = None
    goal: Optional[str] = None
    units: Optional[str] = None


class SnapshotSetDTO(StrictSnapshotModel):
    weight_kg: Annotated[
        float,
        Field(ge=0, allow_inf_nan=False, strict=True),
    ]
    reps: Annotated[int, Field(ge=0, strict=True)]
    reps_max: Optional[Annotated[int, Field(ge=0, strict=True)]] = None


class SnapshotExerciseDTO(StrictSnapshotModel):
    exercise_id: str
    name: str
    note: str
    sets: list[SnapshotSetDTO] = Field(max_length=MAX_SNAPSHOT_ELEMENTS)
    rest_seconds: Optional[Annotated[int, Field(ge=0, strict=True)]] = None
    warmup_sets: Optional[Annotated[int, Field(ge=0, strict=True)]] = None
    warmup_weight_kg: Optional[
        Annotated[float, Field(ge=0, allow_inf_nan=False, strict=True)]
    ] = None
    bilateral: StrictBool


class SnapshotCardioTargetDTO(StrictSnapshotModel):
    duration_min: Optional[Annotated[int, Field(ge=0, strict=True)]] = None
    distance_km: Optional[
        Annotated[float, Field(gt=0, allow_inf_nan=False, strict=True)]
    ] = None


class SnapshotPlanSessionDTO(StrictSnapshotModel):
    session_id: str
    name: str
    kind: Literal["strength", "cardio"]
    cardio_target: Optional[SnapshotCardioTargetDTO] = None
    exercises: list[SnapshotExerciseDTO] = Field(max_length=MAX_SNAPSHOT_ELEMENTS)


class SnapshotPlanDTO(StrictSnapshotModel):
    plan_id: str
    base_plan_fingerprint: str = Field(
        pattern=r"^sha256:[0-9a-f]{64}$",
    )
    name: str
    active: StrictBool
    source: str
    sessions: list[SnapshotPlanSessionDTO] = Field(max_length=MAX_SNAPSHOT_ELEMENTS)


class Snapshot(StrictSnapshotModel):
    generated_at: ISODateTime
    profile: Optional[ProfileDTO] = None
    recovery: Optional[RecoveryToday] = None
    hrv_trend: list[HrvPoint] = Field(
        default_factory=list,
        max_length=MAX_SNAPSHOT_ELEMENTS,
    )
    sleep: list[SleepNightDTO] = Field(
        default_factory=list,
        max_length=MAX_SNAPSHOT_ELEMENTS,
    )
    workouts: list[WorkoutDTO] = Field(
        default_factory=list,
        max_length=MAX_SNAPSHOT_ELEMENTS,
    )
    workouts_scope: Optional[Literal["unconfirmed", "health_unconfirmed"]] = None
    active_plan_name: Optional[str] = None
    plans: Optional[list[SnapshotPlanDTO]] = Field(
        default=None,
        max_length=MAX_SNAPSHOT_ELEMENTS,
    )

    @model_validator(mode="before")
    @classmethod
    def validate_workouts_scope(cls, value: Any) -> Any:
        if not isinstance(value, dict) or "workouts_scope" not in value:
            return value
        scope = value["workouts_scope"]
        if scope == "unconfirmed":
            if "workouts" in value:
                raise ValueError("unconfirmed workouts cannot include a replacement")
            return value
        if scope == "health_unconfirmed":
            workouts = value.get("workouts")
            if not isinstance(workouts, list):
                raise ValueError("health_unconfirmed requires a complete local workout list")
            for workout in workouts:
                if (
                    not isinstance(workout, dict)
                    or workout.get("source") != "local"
                    or not isinstance(workout.get("id"), str)
                    or not workout["id"]
                ):
                    raise ValueError(
                        "health_unconfirmed workouts require a stable local identity"
                    )
            return value
        raise ValueError("unsupported workouts_scope")

    @model_validator(mode="after")
    def enforce_total_collection_limit(self) -> "Snapshot":
        total = len(self.hrv_trend) + len(self.sleep) + len(self.workouts)
        if self.plans is not None:
            total += len(self.plans)
            for plan in self.plans:
                total += len(plan.sessions)
                for session in plan.sessions:
                    if session.cardio_target is not None:
                        total += 1
                    total += len(session.exercises)
                    total += sum(len(exercise.sets) for exercise in session.exercises)
        if total > MAX_SNAPSHOT_ELEMENTS:
            raise ValueError(
                f"snapshot exceeds the {MAX_SNAPSHOT_ELEMENTS}-element transport limit"
            )
        return self
