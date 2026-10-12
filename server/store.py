"""SQLite persistence for Kore snapshots and origin-bound plan proposals."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
import uuid
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from origin_ticket import VerifiedOrigin
from plan_contract import (
    InvalidPlanProposal,
    validate_and_normalize_plan,
    validate_plan_id,
)


MAX_PENDING_PLANS_PER_PRINCIPAL = 4
_INSTANCE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._~-]{0,127}$")
WorkoutUpdateMode = Literal["replace", "preserve", "replace_local"]
# Snapshot v2 (contract/snapshot-v2.md): a snapshot without one of these lists keeps the stored
# list, so an app (or a sync) that does not send them never wipes them; [] clears it.
DAILY_LISTS = ("steps", "body", "nutrition")


def _workout_timestamp(workout: Any) -> datetime:
    if not isinstance(workout, Mapping):
        return datetime.min.replace(tzinfo=timezone.utc)
    raw_timestamp = workout.get("date")
    if not isinstance(raw_timestamp, str):
        return datetime.min.replace(tzinfo=timezone.utc)
    normalized = (
        f"{raw_timestamp[:-1]}+00:00"
        if raw_timestamp.endswith("Z")
        else raw_timestamp
    )
    try:
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return datetime.min.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (OverflowError, ValueError):
        return datetime.min.replace(tzinfo=timezone.utc)


def _deduplicate_workouts_by_id(workouts: list[Any]) -> list[Any]:
    result: list[Any] = []
    seen_ids: set[str] = set()
    for workout in workouts:
        workout_id = workout.get("id") if isinstance(workout, Mapping) else None
        if not isinstance(workout_id, str):
            result.append(workout)
            continue
        if workout_id in seen_ids:
            continue
        seen_ids.add(workout_id)
        result.append(workout)
    return sorted(result, key=_workout_timestamp, reverse=True)


class OriginReplayError(ValueError):
    """The one-time origin nonce was already consumed."""


class PendingPlanLimitError(ValueError):
    """The app-safe pending proposal limit for this principal was reached."""


class StoreConfigurationError(RuntimeError):
    """The process lacks an explicit, safe single-instance store configuration."""


class InstanceOwnershipError(RuntimeError):
    """The configured process does not own this database or requested principal."""


def _validated_identifier(value: str, *, name: str) -> str:
    if not isinstance(value, str) or _INSTANCE_IDENTIFIER.fullmatch(value) is None:
        raise StoreConfigurationError(
            f"{name} must be an explicit safe ASCII instance identifier"
        )
    return value


def _validated_db_path(value: os.PathLike[str] | str) -> str:
    if not isinstance(value, (str, os.PathLike)):
        raise StoreConfigurationError("KORE_DB_PATH must be an explicit absolute path")
    path = Path(value)
    if not path.is_absolute():
        raise StoreConfigurationError("KORE_DB_PATH must be an explicit absolute path")
    return str(path)


class KoreStore:
    def __init__(
        self,
        db_path: os.PathLike[str] | str,
        *,
        instance_id: str,
        principal: str,
        legacy_owner_confirmation: str | None = None,
    ) -> None:
        self.db_path = _validated_db_path(db_path)
        self.instance_id = _validated_identifier(
            instance_id,
            name="KORE_INSTANCE_ID",
        )
        self.principal = _validated_identifier(
            principal,
            name="KORE_PLAN_ORIGIN_PRINCIPAL",
        )
        self.legacy_owner_confirmation = legacy_owner_confirmation

    @property
    def expected_legacy_owner_confirmation(self) -> str:
        return f"{self.instance_id}:{self.principal}"

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=10)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _table_exists(db: sqlite3.Connection, table: str) -> bool:
        return db.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone() is not None

    @classmethod
    def _database_contains_private_rows(cls, db: sqlite3.Connection) -> bool:
        for table in ("snapshot", "plan", "consumed_origin_nonce"):
            if cls._table_exists(db, table) and db.execute(
                f'SELECT 1 FROM "{table}" LIMIT 1'
            ).fetchone() is not None:
                return True
        return False

    def _reject_foreign_stored_principals(self, db: sqlite3.Connection) -> None:
        checks = (
            ("plan", "origin_principal"),
            ("consumed_origin_nonce", "principal"),
        )
        for table, column in checks:
            if not self._table_exists(db, table):
                continue
            columns = {
                row["name"]
                for row in db.execute(f'PRAGMA table_info("{table}")').fetchall()
            }
            if column not in columns:
                continue
            principals = {
                row[0]
                for row in db.execute(
                    f'SELECT DISTINCT "{column}" FROM "{table}" '
                    f'WHERE "{column}" IS NOT NULL'
                ).fetchall()
            }
            foreign = principals - {self.principal}
            if foreign:
                raise InstanceOwnershipError(
                    f"database contains rows owned by another principal in {table}"
                )

    def _ensure_instance_owner(
        self,
        db: sqlite3.Connection,
        *,
        contained_private_rows: bool,
    ) -> None:
        db.execute(
            "CREATE TABLE IF NOT EXISTS kore_instance_owner ("
            "singleton INTEGER PRIMARY KEY CHECK(singleton = 1), "
            "instance_id TEXT NOT NULL, principal TEXT NOT NULL)"
        )
        rows = db.execute(
            "SELECT singleton, instance_id, principal FROM kore_instance_owner"
        ).fetchall()
        if rows:
            if len(rows) != 1 or tuple(rows[0]) != (
                1,
                self.instance_id,
                self.principal,
            ):
                raise InstanceOwnershipError(
                    "database owner does not match KORE_INSTANCE_ID and principal"
                )
            return

        if (
            contained_private_rows
            and self.legacy_owner_confirmation
            != self.expected_legacy_owner_confirmation
        ):
            raise InstanceOwnershipError(
                "legacy database has no owner; set KORE_LEGACY_DB_OWNER_CONFIRMATION "
                "to the exact documented instance:principal value for one audited migration"
            )
        db.execute(
            "INSERT INTO kore_instance_owner(singleton, instance_id, principal) "
            "VALUES (1, ?, ?)",
            (self.instance_id, self.principal),
        )

    def _ensure_snapshot_schema(self, db: sqlite3.Connection) -> None:
        if not self._table_exists(db, "snapshot"):
            db.execute(
                "CREATE TABLE snapshot ("
                "id INTEGER PRIMARY KEY CHECK(id = 1), "
                "instance_id TEXT NOT NULL, principal TEXT NOT NULL, "
                "data TEXT NOT NULL, updated_at REAL NOT NULL)"
            )
            return

        columns = {
            row["name"] for row in db.execute("PRAGMA table_info(snapshot)").fetchall()
        }
        if not {"id", "data", "updated_at"} <= columns:
            raise InstanceOwnershipError("snapshot table has an unsupported schema")
        owner_columns = {"instance_id", "principal"} & columns
        if owner_columns and owner_columns != {"instance_id", "principal"}:
            raise InstanceOwnershipError("snapshot table has an incomplete owner binding")
        if not owner_columns:
            db.execute("ALTER TABLE snapshot ADD COLUMN instance_id TEXT")
            db.execute("ALTER TABLE snapshot ADD COLUMN principal TEXT")
            db.execute(
                "UPDATE snapshot SET instance_id = ?, principal = ? "
                "WHERE instance_id IS NULL AND principal IS NULL",
                (self.instance_id, self.principal),
            )

        rows = db.execute(
            "SELECT id, instance_id, principal FROM snapshot"
        ).fetchall()
        if len(rows) > 1 or any(
            tuple(row) != (1, self.instance_id, self.principal) for row in rows
        ):
            raise InstanceOwnershipError(
                "snapshot row owner does not match this configured instance"
            )

    def _verify_owner(self, db: sqlite3.Connection) -> None:
        try:
            rows = db.execute(
                "SELECT singleton, instance_id, principal FROM kore_instance_owner"
            ).fetchall()
        except sqlite3.OperationalError as error:
            raise InstanceOwnershipError("database has not been owner-initialized") from error
        if len(rows) != 1 or tuple(rows[0]) != (
            1,
            self.instance_id,
            self.principal,
        ):
            raise InstanceOwnershipError(
                "database owner does not match this configured instance"
            )

    def _require_principal(self, principal: str) -> None:
        if principal != self.principal:
            raise InstanceOwnershipError(
                "requested principal does not own this database instance"
            )

    def init(self) -> None:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            contained_private_rows = self._database_contains_private_rows(db)
            self._reject_foreign_stored_principals(db)
            self._ensure_instance_owner(
                db,
                contained_private_rows=contained_private_rows,
            )
            self._ensure_snapshot_schema(db)
            db.execute(
                "CREATE TABLE IF NOT EXISTS plan ("
                "id TEXT PRIMARY KEY, data TEXT NOT NULL, status TEXT NOT NULL, "
                "created_at REAL NOT NULL, acked_at REAL)"
            )
            columns = {
                row["name"] for row in db.execute("PRAGMA table_info(plan)").fetchall()
            }
            if "origin_principal" not in columns:
                db.execute("ALTER TABLE plan ADD COLUMN origin_principal TEXT")
            if "conversation_id" not in columns:
                db.execute("ALTER TABLE plan ADD COLUMN conversation_id TEXT")
            db.execute(
                "CREATE TABLE IF NOT EXISTS consumed_origin_nonce ("
                "nonce TEXT PRIMARY KEY, principal TEXT NOT NULL, "
                "consumed_at REAL NOT NULL, expires_at INTEGER NOT NULL)"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS plan_pending_principal_idx "
                "ON plan(status, origin_principal)"
            )
            self._reject_foreign_stored_principals(db)

    def save_snapshot(
        self,
        data: dict[str, Any],
        *,
        workout_update: WorkoutUpdateMode = "replace",
        validate_final_payload: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._verify_owner(db)
            payload = dict(data)
            absent_daily_lists = [key for key in DAILY_LISTS if key not in payload]
            if workout_update != "replace" or absent_daily_lists:
                if workout_update == "preserve" and "workouts" in payload:
                    raise ValueError(
                        "preserved workout snapshot cannot include a replacement"
                    )
                if workout_update == "replace_local" and not isinstance(
                    payload.get("workouts"), list
                ):
                    raise ValueError(
                        "local workout replacement requires a complete list"
                    )
                row = db.execute(
                    "SELECT data FROM snapshot WHERE id = 1 "
                    "AND instance_id = ? AND principal = ?",
                    (self.instance_id, self.principal),
                ).fetchone()
                if row is not None:
                    try:
                        previous = json.loads(row["data"])
                    except (TypeError, json.JSONDecodeError) as error:
                        raise InstanceOwnershipError(
                            "stored snapshot is not valid JSON"
                        ) from error
                    if not isinstance(previous, dict):
                        raise InstanceOwnershipError(
                            "stored snapshot is not a JSON object"
                        )
                    previous_workouts = previous.get("workouts", [])
                    if not isinstance(previous_workouts, list):
                        raise InstanceOwnershipError(
                            "stored snapshot workouts are not a list"
                        )
                    if workout_update == "preserve" and "workouts" in previous:
                        payload["workouts"] = previous_workouts
                    elif workout_update == "replace_local":
                        replacement = payload["workouts"]
                        retained = [
                            workout
                            for workout in previous_workouts
                            if not (
                                isinstance(workout, Mapping)
                                and workout.get("source") == "local"
                            )
                        ]
                        payload["workouts"] = _deduplicate_workouts_by_id(
                            [*replacement, *retained]
                        )
                    for key in absent_daily_lists:
                        if key in previous:
                            payload[key] = previous[key]
            if validate_final_payload is not None:
                validate_final_payload(payload)
            encoded = json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            )
            result = db.execute(
                "INSERT INTO snapshot("
                "id, instance_id, principal, data, updated_at) VALUES (1, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET data=excluded.data, "
                "updated_at=excluded.updated_at "
                "WHERE snapshot.instance_id=excluded.instance_id "
                "AND snapshot.principal=excluded.principal",
                (self.instance_id, self.principal, encoded, time.time()),
            )
            if result.rowcount != 1:
                raise InstanceOwnershipError(
                    "snapshot row is owned by another configured instance"
                )

    def get_snapshot(self) -> dict[str, Any]:
        with self._connect() as db:
            self._verify_owner(db)
            row = db.execute(
                "SELECT instance_id, principal, data, updated_at "
                "FROM snapshot WHERE id = 1"
            ).fetchone()
        if row is None:
            return {}
        if (row["instance_id"], row["principal"]) != (
            self.instance_id,
            self.principal,
        ):
            raise InstanceOwnershipError(
                "snapshot row is owned by another configured instance"
            )
        value = json.loads(row["data"])
        if not isinstance(value, dict):
            return {}
        value["_updated_at"] = row["updated_at"]
        return value

    def add_plan(self, plan: dict[str, Any], origin: VerifiedOrigin) -> str:
        if not isinstance(origin, VerifiedOrigin):
            raise TypeError("a verified origin is required")
        self._require_principal(origin.principal)
        normalized_plan = validate_and_normalize_plan(plan)
        plan_id = str(uuid.uuid4())
        stored_plan = dict(normalized_plan)
        stored_plan["plan_id"] = plan_id
        stored_plan["origin"] = {
            "version": 1,
            "conversation_id": origin.conversation_id,
        }
        encoded_plan = json.dumps(
            stored_plan,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )

        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                self._verify_owner(db)
                try:
                    db.execute(
                        "INSERT INTO consumed_origin_nonce("
                        "nonce, principal, consumed_at, expires_at) VALUES (?, ?, ?, ?)",
                        (
                            origin.nonce,
                            origin.principal,
                            time.time(),
                            origin.expires_at,
                        ),
                    )
                except sqlite3.IntegrityError as error:
                    if db.execute(
                        "SELECT 1 FROM consumed_origin_nonce WHERE nonce = ?",
                        (origin.nonce,),
                    ).fetchone():
                        raise OriginReplayError(
                            "origin ticket nonce was already consumed"
                        ) from error
                    raise
                pending_count = db.execute(
                    "SELECT COUNT(*) FROM plan "
                    "WHERE status = 'pending' AND origin_principal = ?",
                    (origin.principal,),
                ).fetchone()[0]
                if pending_count >= MAX_PENDING_PLANS_PER_PRINCIPAL:
                    raise PendingPlanLimitError(
                        "acknowledge an existing pending plan before proposing another"
                    )
                db.execute(
                    "INSERT INTO plan("
                    "id, data, status, created_at, acked_at, "
                    "origin_principal, conversation_id) "
                    "VALUES (?, ?, 'pending', ?, NULL, ?, ?)",
                    (
                        plan_id,
                        encoded_plan,
                        time.time(),
                        origin.principal,
                        origin.conversation_id,
                    ),
                )
                db.commit()
            except Exception:
                db.rollback()
                raise
        return plan_id

    def pending_plans(self, principal: str) -> list[dict[str, Any]]:
        self._require_principal(principal)
        with self._connect() as db:
            self._verify_owner(db)
            rows = db.execute(
                "SELECT id, data, conversation_id FROM plan "
                "WHERE status = 'pending' AND origin_principal = ? "
                "AND conversation_id IS NOT NULL ORDER BY created_at, id",
                (principal,),
            ).fetchall()
        plans: list[dict[str, Any]] = []
        for row in rows:
            try:
                data = validate_and_normalize_plan(
                    json.loads(row["data"]),
                    allow_server_envelope=True,
                )
                plan_id = validate_plan_id(row["id"])
                parsed_id = uuid.UUID(row["conversation_id"])
            except (
                InvalidPlanProposal,
                json.JSONDecodeError,
                TypeError,
                ValueError,
                AttributeError,
            ):
                continue
            if (
                not isinstance(data, dict)
                or str(parsed_id) != row["conversation_id"]
                or parsed_id.variant != uuid.RFC_4122
            ):
                continue
            data["plan_id"] = plan_id
            data["origin"] = {
                "version": 1,
                "conversation_id": row["conversation_id"],
            }
            plans.append(data)
            if len(plans) == MAX_PENDING_PLANS_PER_PRINCIPAL:
                break
        return plans

    def ack_plan(self, plan_id: str, accepted: bool, principal: str) -> bool:
        if type(accepted) is not bool:
            raise TypeError("accepted must be a boolean")
        self._require_principal(principal)
        status = "accepted" if accepted else "rejected"
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._verify_owner(db)
            row = db.execute(
                "SELECT status FROM plan WHERE id = ? AND origin_principal = ?",
                (plan_id, principal),
            ).fetchone()
            if row is None:
                return False
            if row["status"] == status:
                return True
            if row["status"] != "pending":
                return False
            result = db.execute(
                "UPDATE plan SET status = ?, acked_at = ? "
                "WHERE id = ? AND status = 'pending' AND origin_principal = ?",
                (status, time.time(), plan_id, principal),
            )
        return result.rowcount == 1


def _required_environment_value(environment: Mapping[str, str], name: str) -> str:
    value = environment.get(name)
    if not value:
        raise StoreConfigurationError(
            f"required environment variable {name} is missing"
        )
    return value


def _store_from_environment(environment: Mapping[str, str]) -> KoreStore:
    return KoreStore(
        _required_environment_value(environment, "KORE_DB_PATH"),
        instance_id=_required_environment_value(environment, "KORE_INSTANCE_ID"),
        principal=_required_environment_value(
            environment,
            "KORE_PLAN_ORIGIN_PRINCIPAL",
        ),
        legacy_owner_confirmation=environment.get(
            "KORE_LEGACY_DB_OWNER_CONFIRMATION"
        ),
    )


_default_store: KoreStore | None = None


def _configured_store() -> KoreStore:
    global _default_store
    if _default_store is None:
        _default_store = _store_from_environment(os.environ)
    return _default_store


def init() -> None:
    _configured_store().init()


def save_snapshot(
    data: dict[str, Any],
    *,
    workout_update: WorkoutUpdateMode = "replace",
    validate_final_payload: Callable[[dict[str, Any]], None] | None = None,
) -> None:
    _configured_store().save_snapshot(
        data,
        workout_update=workout_update,
        validate_final_payload=validate_final_payload,
    )


def get_snapshot() -> dict[str, Any]:
    return _configured_store().get_snapshot()


def add_plan(plan: dict[str, Any], origin: VerifiedOrigin) -> str:
    return _configured_store().add_plan(plan, origin)


def pending_plans(principal: str) -> list[dict[str, Any]]:
    return _configured_store().pending_plans(principal)


def ack_plan(plan_id: str, accepted: bool, principal: str) -> bool:
    return _configured_store().ack_plan(plan_id, accepted, principal)
