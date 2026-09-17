"""Stdlib-only normalization for the exact Swift pending-plan contract."""

from __future__ import annotations

import math
import uuid
from collections.abc import Mapping
from typing import Any


SCHEMA_VERSION = "1.0"
MAXIMUM_PENDING_RESPONSE_BYTES = 16 * 1_024 * 1_024
PLAN_ID_BYTES = 128
PLAN_NAME_BYTES = 128
RATIONALE_BYTES = 2 * 1_024
REPLACEMENT_NAME_BYTES = 128
PLAN_FINGERPRINT_BYTES = 71
SESSIONS_PER_PLAN = 14
SESSION_NAME_BYTES = 128
EXERCISES_PER_SESSION = 32
EXERCISE_NAME_BYTES = 128
EXERCISE_NOTE_BYTES = 512
SETS_PER_EXERCISE = 20
MAXIMUM_WEIGHT_KG = 1_000.0
MAXIMUM_REPETITIONS = 100
MAXIMUM_REST_SECONDS = 300
MAXIMUM_WARMUP_SETS = 5
MAXIMUM_WARMUP_WEIGHT_KG = 500.0
MINIMUM_CARDIO_DURATION_MINUTES = 10
MAXIMUM_CARDIO_DURATION_MINUTES = 1_440
MAXIMUM_CARDIO_DISTANCE_KM = 1_000.0

_PLAN_PROPOSAL_KEYS = frozenset(
    {
        "schema_version",
        "name",
        "rationale",
        "replaces_plan",
        "replaces_plan_id",
        "base_plan_fingerprint",
        "removed_session_ids",
        "removed_exercise_ids",
        "sessions",
    }
)
_PLAN_ENVELOPE_KEYS = frozenset(
    {
        # Added by the server only after proposal validation. Pending-read
        # validation reconstructs both values from authoritative SQLite columns.
        "plan_id",
        "origin",
    }
)
_SESSION_KEYS = frozenset(
    {"session_id", "name", "kind", "cardio_target", "exercises"}
)
_CARDIO_TARGET_KEYS = frozenset({"duration_min", "distance_km"})
_EXERCISE_KEYS = frozenset(
    {
        "exercise_id",
        "name",
        "note",
        "sets",
        "rest_seconds",
        "warmup_sets",
        "warmup_weight_kg",
        "bilateral",
    }
)
_SET_KEYS = frozenset({"weight_kg", "reps", "reps_max"})

# Exact scalar membership of Foundation's CharacterSet.whitespacesAndNewlines on
# the supported Swift/Xcode runtime. Swift's plan parser uses that set to decide
# whether required text is blank; Python str.strip() notably omits U+200B and
# includes U+001C..U+001F, so it cannot safely stand in for the client contract.
_FOUNDATION_WHITESPACES_AND_NEWLINES = frozenset(
    chr(code_point)
    for code_point in (
        *range(0x0009, 0x000E),
        0x0020,
        0x0085,
        0x00A0,
        0x1680,
        *range(0x2000, 0x200C),
        0x2028,
        0x2029,
        0x202F,
        0x205F,
        0x3000,
    )
)


class InvalidPlanProposal(ValueError):
    """A plan cannot be serialized into a response accepted by the Swift client."""


def validate_plan_id(value: Any) -> str:
    if type(value) is not str:
        raise InvalidPlanProposal("plan_id must be a string")
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise InvalidPlanProposal("plan_id must be valid UTF-8") from error
    if (
        not encoded
        or len(encoded) > PLAN_ID_BYTES
        or any(byte < 0x21 or byte > 0x7E for byte in encoded)
        or value in {".", ".."}
        or any(character in value for character in ("/", "\\", "%"))
    ):
        raise InvalidPlanProposal("plan_id is not a safe Swift ACK path segment")
    return value


def validate_and_normalize_plan(
    value: Any,
    *,
    allow_server_envelope: bool = False,
) -> dict[str, Any]:
    plan = _mapping(value, "plan")
    allowed_plan_keys = _PLAN_PROPOSAL_KEYS
    if allow_server_envelope:
        allowed_plan_keys = allowed_plan_keys | _PLAN_ENVELOPE_KEYS
    _reject_unknown_keys(plan, "plan", allowed_plan_keys)
    schema_version = plan.get("schema_version", SCHEMA_VERSION)
    if type(schema_version) is not str or schema_version != SCHEMA_VERSION:
        raise InvalidPlanProposal("schema_version must be '1.0'")

    normalized: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "name": _bounded_text(
            plan.get("name"), "plan name", PLAN_NAME_BYTES, nonblank=True
        ),
        "rationale": _bounded_text(
            plan.get("rationale", ""),
            "rationale",
            RATIONALE_BYTES,
            nonblank=False,
        ),
    }
    if "replaces_plan" in plan:
        normalized["replaces_plan"] = _bounded_text(
            plan["replaces_plan"],
            "replaces_plan",
            REPLACEMENT_NAME_BYTES,
            nonblank=True,
        )
    if "replaces_plan_id" in plan:
        normalized["replaces_plan_id"] = _canonical_uuid(
            plan["replaces_plan_id"],
            "replaces_plan_id",
        )

    declares_update_target = (
        "replaces_plan" in normalized or "replaces_plan_id" in normalized
    )
    update_contract_keys = (
        "base_plan_fingerprint",
        "removed_session_ids",
        "removed_exercise_ids",
    )
    if declares_update_target:
        if any(key not in plan for key in update_contract_keys):
            raise InvalidPlanProposal(
                "plan updates require base fingerprint and explicit removal arrays"
            )
        normalized["base_plan_fingerprint"] = _plan_fingerprint(
            plan["base_plan_fingerprint"]
        )
        normalized["removed_session_ids"] = _unique_uuid_list(
            plan["removed_session_ids"],
            "removed_session_ids",
            maximum=SESSIONS_PER_PLAN,
        )
        normalized["removed_exercise_ids"] = _unique_uuid_list(
            plan["removed_exercise_ids"],
            "removed_exercise_ids",
            maximum=SESSIONS_PER_PLAN * EXERCISES_PER_SESSION,
        )
    elif any(key in plan for key in update_contract_keys):
        raise InvalidPlanProposal("new plans must not contain update-only fields")

    raw_sessions = _bounded_list(
        plan.get("sessions"), "sessions", minimum=1, maximum=SESSIONS_PER_PLAN
    )
    normalized_sessions: list[dict[str, Any]] = []
    explicit_session_ids: set[str] = set()
    explicit_exercise_ids: set[str] = set()
    for raw_session in raw_sessions:
        session = _mapping(raw_session, "session")
        _reject_unknown_keys(session, "session", _SESSION_KEYS)
        normalized_session: dict[str, Any] = {}

        raw_session_id = session.get("session_id")
        if raw_session_id is not None:
            session_id = _canonical_uuid(raw_session_id)
            if session_id in explicit_session_ids:
                raise InvalidPlanProposal("explicit session_id values must be unique")
            explicit_session_ids.add(session_id)
            normalized_session["session_id"] = session_id

        normalized_session["name"] = _bounded_text(
            session.get("name"),
            "session name",
            SESSION_NAME_BYTES,
            nonblank=True,
        )
        raw_kind = session.get("kind")
        if raw_kind is None:
            kind = None
        elif type(raw_kind) is str and raw_kind in {"strength", "cardio"}:
            kind = raw_kind
            normalized_session["kind"] = kind
        else:
            raise InvalidPlanProposal("session kind must be strength or cardio")

        if "exercises" in session:
            raw_exercises_value = session["exercises"]
        else:
            raw_exercises_value = []
        raw_exercises = _bounded_list(
            raw_exercises_value,
            "exercises",
            minimum=0,
            maximum=EXERCISES_PER_SESSION,
        )
        if kind != "cardio" and not raw_exercises:
            raise InvalidPlanProposal("strength sessions require at least one exercise")
        if kind == "cardio" and raw_exercises:
            raise InvalidPlanProposal("cardio sessions must not contain exercises")

        raw_target = session.get("cardio_target")
        if raw_target is not None:
            if kind != "cardio":
                raise InvalidPlanProposal("cardio_target requires kind cardio")
            target = _mapping(raw_target, "cardio_target")
            _reject_unknown_keys(
                target,
                "cardio_target",
                _CARDIO_TARGET_KEYS,
            )
            normalized_target: dict[str, Any] = {}
            duration = target.get("duration_min")
            if duration is not None:
                exact_duration = _exact_integer(duration, "cardio duration")
                if not (
                    MINIMUM_CARDIO_DURATION_MINUTES
                    <= exact_duration
                    <= MAXIMUM_CARDIO_DURATION_MINUTES
                ):
                    raise InvalidPlanProposal("cardio duration is outside Swift bounds")
                normalized_target["duration_min"] = exact_duration
            distance = target.get("distance_km")
            if distance is not None:
                exact_distance = _finite_number(distance, "cardio distance")
                if not 0 < exact_distance <= MAXIMUM_CARDIO_DISTANCE_KM:
                    raise InvalidPlanProposal("cardio distance is outside Swift bounds")
                normalized_target["distance_km"] = exact_distance
            normalized_session["cardio_target"] = normalized_target

        normalized_exercises: list[dict[str, Any]] = []
        for raw_exercise in raw_exercises:
            exercise = _mapping(raw_exercise, "exercise")
            _reject_unknown_keys(exercise, "exercise", _EXERCISE_KEYS)
            normalized_exercise: dict[str, Any] = {}

            if "exercise_id" in exercise:
                exercise_id = _canonical_uuid(
                    exercise["exercise_id"],
                    "exercise_id",
                )
                if exercise_id in explicit_exercise_ids:
                    raise InvalidPlanProposal(
                        "explicit exercise_id values must be unique"
                    )
                explicit_exercise_ids.add(exercise_id)
                normalized_exercise["exercise_id"] = exercise_id

            raw_sets = _bounded_list(
                exercise.get("sets"),
                "sets",
                minimum=1,
                maximum=SETS_PER_EXERCISE,
            )
            normalized_sets: list[dict[str, Any]] = []
            for raw_set in raw_sets:
                plan_set = _mapping(raw_set, "set")
                _reject_unknown_keys(plan_set, "set", _SET_KEYS)
                weight = _finite_number(plan_set.get("weight_kg", 0), "weight_kg")
                repetitions = _exact_integer(plan_set.get("reps", 1), "reps")
                if not 0 <= weight <= MAXIMUM_WEIGHT_KG:
                    raise InvalidPlanProposal("weight_kg is outside Swift bounds")
                if not 1 <= repetitions <= MAXIMUM_REPETITIONS:
                    raise InvalidPlanProposal("reps is outside Swift bounds")
                normalized_set = {"weight_kg": weight, "reps": repetitions}
                if "reps_max" in plan_set:
                    repetitions_maximum = _exact_integer(
                        plan_set["reps_max"],
                        "reps_max",
                    )
                    if not repetitions < repetitions_maximum <= MAXIMUM_REPETITIONS:
                        raise InvalidPlanProposal(
                            "reps_max must be greater than reps and within Swift bounds"
                        )
                    normalized_set["reps_max"] = repetitions_maximum
                normalized_sets.append(normalized_set)

            normalized_exercise.update(
                {
                    "name": _bounded_text(
                        exercise.get("name"),
                        "exercise name",
                        EXERCISE_NAME_BYTES,
                        nonblank=True,
                    ),
                    "sets": normalized_sets,
                }
            )
            if "note" in exercise:
                normalized_exercise["note"] = _bounded_text(
                    exercise["note"],
                    "exercise note",
                    EXERCISE_NOTE_BYTES,
                    nonblank=False,
                )
            if "rest_seconds" in exercise:
                rest_seconds = _exact_integer(
                    exercise["rest_seconds"],
                    "rest_seconds",
                )
                if not 0 <= rest_seconds <= MAXIMUM_REST_SECONDS:
                    raise InvalidPlanProposal(
                        "rest_seconds is outside Swift editor bounds"
                    )
                normalized_exercise["rest_seconds"] = rest_seconds
            if "warmup_sets" in exercise:
                warmup_sets = _exact_integer(
                    exercise["warmup_sets"],
                    "warmup_sets",
                )
                if not 0 <= warmup_sets <= MAXIMUM_WARMUP_SETS:
                    raise InvalidPlanProposal(
                        "warmup_sets is outside Swift editor bounds"
                    )
                normalized_exercise["warmup_sets"] = warmup_sets
            if "warmup_weight_kg" in exercise:
                warmup_weight = _finite_number(
                    exercise["warmup_weight_kg"],
                    "warmup_weight_kg",
                )
                if not 0 <= warmup_weight <= MAXIMUM_WARMUP_WEIGHT_KG:
                    raise InvalidPlanProposal(
                        "warmup_weight_kg is outside Swift editor bounds"
                    )
                normalized_exercise["warmup_weight_kg"] = warmup_weight
            if "bilateral" in exercise:
                bilateral = exercise["bilateral"]
                if type(bilateral) is not bool:
                    raise InvalidPlanProposal("bilateral must be a boolean")
                normalized_exercise["bilateral"] = bilateral

            normalized_exercises.append(normalized_exercise)
        normalized_session["exercises"] = normalized_exercises
        normalized_sessions.append(normalized_session)

    normalized["sessions"] = normalized_sessions
    return normalized


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise InvalidPlanProposal(f"{field} must be an object")
    return value


def _reject_unknown_keys(
    value: Mapping[str, Any],
    field: str,
    allowed: frozenset[str],
) -> None:
    unknown = [key for key in value if type(key) is not str or key not in allowed]
    if unknown:
        raise InvalidPlanProposal(f"{field} contains unsupported fields")


def _bounded_list(
    value: Any,
    field: str,
    *,
    minimum: int,
    maximum: int,
) -> list[Any]:
    if type(value) is not list or not minimum <= len(value) <= maximum:
        raise InvalidPlanProposal(
            f"{field} must contain from {minimum} through {maximum} items"
        )
    return value


def _bounded_text(
    value: Any,
    field: str,
    maximum_bytes: int,
    *,
    nonblank: bool,
) -> str:
    if type(value) is not str:
        raise InvalidPlanProposal(f"{field} must be a string")
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise InvalidPlanProposal(f"{field} must be valid UTF-8") from error
    if len(encoded) > maximum_bytes:
        raise InvalidPlanProposal(f"{field} exceeds its UTF-8 byte limit")
    if nonblank and all(
        scalar in _FOUNDATION_WHITESPACES_AND_NEWLINES for scalar in value
    ):
        raise InvalidPlanProposal(f"{field} must not be blank")
    return value


def _canonical_uuid(value: Any, field: str = "session_id") -> str:
    if type(value) is not str:
        raise InvalidPlanProposal(f"{field} must be a UUID string")
    try:
        return str(uuid.UUID(value))
    except (ValueError, AttributeError) as error:
        raise InvalidPlanProposal(f"{field} must be a UUID string") from error


def _plan_fingerprint(value: Any) -> str:
    if type(value) is not str:
        raise InvalidPlanProposal("base_plan_fingerprint must be a string")
    try:
        encoded = value.encode("ascii")
    except UnicodeEncodeError as error:
        raise InvalidPlanProposal(
            "base_plan_fingerprint must be lowercase sha256"
        ) from error
    if (
        len(encoded) != PLAN_FINGERPRINT_BYTES
        or not value.startswith("sha256:")
        or any(character not in "0123456789abcdef" for character in value[7:])
    ):
        raise InvalidPlanProposal("base_plan_fingerprint must be lowercase sha256")
    return value


def _unique_uuid_list(value: Any, field: str, *, maximum: int) -> list[str]:
    values = _bounded_list(value, field, minimum=0, maximum=maximum)
    normalized = [_canonical_uuid(item, field) for item in values]
    if len(set(normalized)) != len(normalized):
        raise InvalidPlanProposal(f"{field} values must be unique")
    return normalized


def _finite_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidPlanProposal(f"{field} must be a JSON number")
    try:
        result = float(value)
    except (OverflowError, ValueError) as error:
        raise InvalidPlanProposal(f"{field} must be finite") from error
    if not math.isfinite(result):
        raise InvalidPlanProposal(f"{field} must be finite")
    return result


def _exact_integer(value: Any, field: str) -> int:
    number = _finite_number(value, field)
    if not number.is_integer():
        raise InvalidPlanProposal(f"{field} must be an exact integer")
    return int(number)
