from __future__ import annotations

import importlib
import os
import secrets
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path

from fastapi.testclient import TestClient


SERVER_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_ROOT))


class FastAPIIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.token = secrets.token_urlsafe(32)
        os.environ.update(
            {
                "KORE_DB_PATH": str(Path(cls.temp_dir.name) / "api.db"),
                "KORE_INSTANCE_ID": "api-integration",
                "KORE_INGEST_TOKEN": cls.token,
                "KORE_PLAN_ORIGIN_PRINCIPAL": "private-api",
            }
        )
        for name in ("ingest_api", "store", "models", "origin_ticket"):
            sys.modules.pop(name, None)
        cls.ingest_api = importlib.import_module("ingest_api")
        cls.store = importlib.import_module("store")
        cls.origin_ticket = importlib.import_module("origin_ticket")
        cls.client_context = TestClient(cls.ingest_api.app)
        cls.client = cls.client_context.__enter__()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client_context.__exit__(None, None, None)
        cls.temp_dir.cleanup()

    def authorization(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    def test_live_routes_keep_shapes_and_origin_bound_ack_is_idempotent(self) -> None:
        self.assertEqual(self.client.get("/health").json(), {"ok": True})
        self.assertEqual(self.client.get("/plan/pending").status_code, 401)

        snapshot = {
            "generated_at": "2026-07-14T12:00:00Z",
            "profile": {"age": 35, "goal": "kraft", "units": "kg"},
            "recovery": {
                "date": "2026-07-14",
                "readiness": 81,
                "hrv_sdnn_ms": 55.0,
                "resting_hr": 52,
                "sleep_h": 7.5,
                "band": "green",
            },
            "hrv_trend": [{"date": "2026-07-13", "sdnn_ms": 49.0}],
            "sleep": [
                {
                    "date": "2026-07-13",
                    "total_h": 7.5,
                    "deep_h": 1.2,
                    "rem_h": 1.7,
                    "efficiency_pct": 91,
                }
            ],
            "workouts": [
                {
                    "id": str(uuid.uuid4()).upper(),
                    "date": "2026-07-13T17:30:00Z",
                    "type": "Strength",
                    "duration_min": 45,
                    "avg_hr": 132,
                    "volume_kg": 4_200.0,
                    "kcal": 390.0,
                }
            ],
            "active_plan_name": "Hybrid",
            "plans": [
                {
                    "plan_id": str(uuid.uuid4()).upper(),
                    "base_plan_fingerprint": "sha256:" + "a" * 64,
                    "name": "Hybrid",
                    "active": True,
                    "source": "user",
                    "sessions": [
                        {
                            "session_id": str(uuid.uuid4()).upper(),
                            "name": "Strength",
                            "kind": "strength",
                            "exercises": [
                                {
                                    "exercise_id": str(uuid.uuid4()).upper(),
                                    "name": "Squat",
                                    "note": "Barbell",
                                    "sets": [
                                        {"weight_kg": 80.0, "reps": 8, "reps_max": 12}
                                    ],
                                    "rest_seconds": 180,
                                    "warmup_sets": 2,
                                    "warmup_weight_kg": 40.0,
                                    "bilateral": False,
                                }
                            ],
                        },
                        {
                            "session_id": str(uuid.uuid4()).upper(),
                            "name": "Run",
                            "kind": "cardio",
                            "cardio_target": {
                                "duration_min": 30,
                                "distance_km": 5.0,
                            },
                            "exercises": [],
                        },
                    ],
                }
            ],
        }
        response = self.client.post(
            "/ingest", headers=self.authorization(), json=snapshot
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True})
        stored_snapshot = self.store.get_snapshot()
        stored_snapshot.pop("_updated_at")
        self.assertEqual(stored_snapshot, snapshot)

        conversation_id = str(uuid.uuid4())
        origin = self.origin_ticket.VerifiedOrigin(
            version=1,
            conversation_id=conversation_id,
            principal="private-api",
            nonce=secrets.token_urlsafe(24),
            issued_at=int(time.time()),
            expires_at=int(time.time()) + 180,
        )
        plan_id = self.store.add_plan(
            {
                "schema_version": "1.0",
                "name": "Easy Run",
                "sessions": [
                    {
                        "name": "Run",
                        "kind": "cardio",
                        "cardio_target": None,
                        "exercises": [],
                    }
                ],
            },
            origin,
        )

        pending = self.client.get(
            "/plan/pending", headers=self.authorization()
        )
        self.assertEqual(pending.status_code, 200)
        plan = pending.json()["plans"][0]
        self.assertEqual(plan["plan_id"], plan_id)
        self.assertEqual(
            plan["origin"],
            {"version": 1, "conversation_id": conversation_id},
        )
        self.assertNotIn("cardio_target", plan["sessions"][0])

        path = f"/plan/{plan_id}/ack"
        first = self.client.post(
            path, headers=self.authorization(), json={"accepted": True}
        )
        retry = self.client.post(
            path, headers=self.authorization(), json={"accepted": True}
        )
        conflict = self.client.post(
            path, headers=self.authorization(), json={"accepted": False}
        )
        self.assertEqual(first.json(), {"ok": True, "status": "accepted"})
        self.assertEqual(retry.json(), first.json())
        self.assertEqual(conflict.status_code, 404)

    def test_unconfirmed_workout_scope_preserves_atomically_and_validates_strictly(
        self,
    ) -> None:
        workout = {
            "id": str(uuid.uuid4()).upper(),
            "date": "2026-07-13T17:30:00Z",
            "type": "Strength",
            "duration_min": 45,
            "avg_hr": 132,
            "volume_kg": 4_200.0,
            "kcal": 390.0,
        }
        baseline = {
            "generated_at": "2026-07-14T12:00:00Z",
            "profile": {"age": 35, "goal": "kraft", "units": "kg"},
            "workouts": [workout],
        }
        self.assertEqual(
            self.client.post(
                "/ingest",
                headers=self.authorization(),
                json=baseline,
            ).status_code,
            200,
        )

        preserve = {
            "generated_at": "2026-07-15T12:00:00Z",
            "profile": {"age": 36, "goal": "ausdauer", "units": "kg"},
            "workouts_scope": "unconfirmed",
        }
        response = self.client.post(
            "/ingest",
            headers=self.authorization(),
            json=preserve,
        )
        self.assertEqual(response.status_code, 200, response.text)
        stored = self.store.get_snapshot()
        stored.pop("_updated_at")
        self.assertEqual(stored["generated_at"], preserve["generated_at"])
        self.assertEqual(stored["profile"], preserve["profile"])
        self.assertEqual(stored["workouts"], [workout])
        self.assertNotIn("workouts_scope", stored)

        before_invalid = stored
        invalid_payloads = [
            {**preserve, "workouts_scope": None},
            {**preserve, "workouts_scope": "replace"},
            {**preserve, "workouts": []},
        ]
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                rejected = self.client.post(
                    "/ingest",
                    headers=self.authorization(),
                    json=payload,
                )
                self.assertEqual(rejected.status_code, 422, rejected.text)
                unchanged = self.store.get_snapshot()
                unchanged.pop("_updated_at")
                self.assertEqual(unchanged, before_invalid)

        explicit_clear = {
            "generated_at": "2026-07-16T12:00:00Z",
            "workouts": [],
        }
        self.assertEqual(
            self.client.post(
                "/ingest",
                headers=self.authorization(),
                json=explicit_clear,
            ).status_code,
            200,
        )
        cleared = self.store.get_snapshot()
        cleared.pop("_updated_at")
        self.assertEqual(cleared, explicit_clear)

        isolated = self.store.KoreStore(
            Path(self.temp_dir.name) / "preserve-without-baseline.db",
            instance_id="preserve-without-baseline",
            principal="private-api",
        )
        isolated.init()
        isolated.save_snapshot(
            {"generated_at": "2026-07-17T12:00:00Z"},
            workout_update="preserve",
        )
        isolated_snapshot = isolated.get_snapshot()
        isolated_snapshot.pop("_updated_at")
        self.assertEqual(
            isolated_snapshot,
            {"generated_at": "2026-07-17T12:00:00Z"},
        )

    def test_health_unconfirmed_scope_replaces_only_authoritative_local_workouts(
        self,
    ) -> None:
        def workout(
            identifier: str,
            *,
            source: str | None = None,
            duration: int = 30,
            date: str = "2026-07-13T17:30:00Z",
        ) -> dict[str, object]:
            result: dict[str, object] = {
                "id": identifier,
                "date": date,
                "type": "Strength",
                "duration_min": duration,
            }
            if source is not None:
                result["source"] = source
            return result

        retained_health = workout(
            "health-retained",
            source="health",
            date="2026-07-14T17:30:00Z",
        )
        retained_legacy = workout("legacy-retained")
        baseline = {
            "generated_at": "2026-07-14T12:00:00Z",
            "workouts": [
                workout("local-updated", source="local"),
                workout("local-deleted", source="local"),
                retained_health,
                retained_legacy,
                workout("legacy-upgraded"),
            ],
        }
        self.assertEqual(
            self.client.post(
                "/ingest",
                headers=self.authorization(),
                json=baseline,
            ).status_code,
            200,
        )

        local_update = workout("local-updated", source="local", duration=45)
        upgraded_legacy = workout("legacy-upgraded", source="local", duration=50)
        new_local = workout("local-new", source="local", duration=55)
        response = self.client.post(
            "/ingest",
            headers=self.authorization(),
            json={
                "generated_at": "2026-07-15T12:00:00Z",
                "workouts_scope": "health_unconfirmed",
                "workouts": [local_update, upgraded_legacy, new_local],
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        stored = self.store.get_snapshot()
        stored.pop("_updated_at")
        self.assertEqual(stored["generated_at"], "2026-07-15T12:00:00Z")
        self.assertEqual(
            stored["workouts"],
            [retained_health, local_update, upgraded_legacy, new_local, retained_legacy],
        )
        self.assertNotIn("workouts_scope", stored)

        deletion = self.client.post(
            "/ingest",
            headers=self.authorization(),
            json={
                "generated_at": "2026-07-16T12:00:00Z",
                "workouts_scope": "health_unconfirmed",
                "workouts": [],
            },
        )
        self.assertEqual(deletion.status_code, 200, deletion.text)
        after_deletion = self.store.get_snapshot()
        after_deletion.pop("_updated_at")
        self.assertEqual(after_deletion["workouts"], [retained_health, retained_legacy])

        before_invalid = self.store.get_snapshot()
        invalid_payloads = [
            {
                "generated_at": "2026-07-17T12:00:00Z",
                "workouts_scope": "health_unconfirmed",
            },
            {
                "generated_at": "2026-07-17T12:00:00Z",
                "workouts_scope": "health_unconfirmed",
                "workouts": [workout("not-local", source="health")],
            },
            {
                "generated_at": "2026-07-17T12:00:00Z",
                "workouts_scope": "health_unconfirmed",
                "workouts": [workout("missing-source")],
            },
            {
                "generated_at": "2026-07-17T12:00:00Z",
                "workouts_scope": "health_unconfirmed",
                "workouts": [{**workout("temporary", source="local"), "id": ""}],
            },
        ]
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                rejected = self.client.post(
                    "/ingest",
                    headers=self.authorization(),
                    json=payload,
                )
                self.assertEqual(rejected.status_code, 422, rejected.text)
                self.assertEqual(rejected.json(), {"detail": "Invalid request"})
                self.assertEqual(self.store.get_snapshot(), before_invalid)

    def test_health_unconfirmed_merge_revalidates_limit_and_rolls_back(self) -> None:
        workout = {
            "date": "2026-07-13T17:30:00Z",
            "type": "Strength",
            "duration_min": 45,
            "source": "health",
        }
        baseline = {
            "generated_at": "2026-07-14T12:00:00Z",
            "workouts": [
                {**workout, "id": f"health-{index}"}
                for index in range(20_000)
            ],
        }
        self.store.save_snapshot(baseline)
        before = self.store.get_snapshot()

        rejected = self.client.post(
            "/ingest",
            headers=self.authorization(),
            json={
                "generated_at": "2026-07-15T12:00:00Z",
                "workouts_scope": "health_unconfirmed",
                "workouts": [{
                    "id": "local-over-limit",
                    "source": "local",
                    "date": "2026-07-14T17:30:00Z",
                    "type": "Strength",
                    "duration_min": 45,
                }],
            },
        )

        self.assertEqual(rejected.status_code, 422, rejected.text)
        self.assertEqual(rejected.json(), {"detail": "Invalid request"})
        self.assertEqual(self.store.get_snapshot(), before)

    def test_unconfirmed_workout_merge_revalidates_limit_and_rolls_back(self) -> None:
        workout = {
            "id": str(uuid.uuid4()).upper(),
            "date": "2026-07-13T17:30:00Z",
            "type": "Strength",
            "duration_min": 45,
        }
        baseline = {
            "generated_at": "2026-07-14T12:00:00Z",
            "workouts": [workout.copy() for _ in range(20_000)],
        }
        self.store.save_snapshot(baseline)
        before = self.store.get_snapshot()

        rejected = self.client.post(
            "/ingest",
            headers=self.authorization(),
            json={
                "generated_at": "2026-07-15T12:00:00Z",
                "workouts_scope": "unconfirmed",
                "hrv_trend": [{"date": "2026-07-14", "sdnn_ms": 42.0}],
            },
        )

        self.assertEqual(rejected.status_code, 422, rejected.text)
        self.assertEqual(rejected.json(), {"detail": "Invalid request"})
        self.assertEqual(self.store.get_snapshot(), before)

    def test_ingest_preserves_each_hrv_variant_and_provenance_without_aliases(
        self,
    ) -> None:
        recoveries = [
            {
                "date": "2026-07-14",
                "readiness": 81,
                "hrv_rmssd_ms": 54.0,
                "hrv_baseline_ms": 49.0,
                "hrv_method_effective": "rmssd",
                "hrv_kind": "rmssd",
                "hrv_source": "nightHeartbeatSeriesRMSSD",
                "hrv_quality": "good",
                "hrv_sample_count": 18,
                "hrv_contributed_to_recovery": True,
                "resting_hr": 52,
                "sleep_h": 7.5,
                "band": "ready",
            },
            {
                "date": "2026-07-14",
                "hrv_sdnn_ms": 47.0,
                "hrv_method_effective": "sdnnFallbackFromRMSSD",
                "hrv_kind": "sdnn",
                "hrv_source": "sleepHealthKitSDNN",
                "hrv_quality": "fallback",
                "hrv_sample_count": 0,
                "hrv_contributed_to_recovery": False,
                "hrv_fallback_reason": (
                    "Keine gültigen rMSSD-Beat-Serien im Schlafzeitraum — "
                    "Apple-SDNN aus dem Schlaf genutzt."
                ),
            },
            {
                "date": "2026-07-14",
                "hrv_ms": 42.0,
                "hrv_source": "metric_tile_fallback",
            },
            # 0.13.1: Apples Erholungs-HRV (App seit 0.10) — eigene Skala, nur im neutralen hrv_ms.
            {
                "date": "2026-07-14",
                "readiness": 74,
                "hrv_ms": 61.0,
                "hrv_baseline_ms": 58.0,
                "hrv_method_effective": "appleRMSSD",
                "hrv_kind": "appleRMSSD",
                "hrv_source": "appleRecoveryRMSSD",
                "hrv_quality": "good",
                "hrv_sample_count": 46,
                "hrv_contributed_to_recovery": True,
            },
            {
                "date": "2026-07-14",
                "hrv_ms": 60.0,
                "hrv_method_effective": "appleRMSSD",
                "hrv_kind": "appleRMSSD",
                "hrv_source": "metric_tile_fallback",
            },
        ]

        for recovery in recoveries:
            with self.subTest(recovery=recovery):
                response = self.client.post(
                    "/ingest",
                    headers=self.authorization(),
                    json={
                        "generated_at": "2026-07-14T12:00:00Z",
                        "recovery": recovery,
                    },
                )
                self.assertEqual(response.status_code, 200, response.text)
                stored = self.store.get_snapshot()
                self.assertEqual(stored["recovery"], recovery)
                self.assertEqual(
                    [
                        key
                        for key in ("hrv_rmssd_ms", "hrv_sdnn_ms", "hrv_ms")
                        if key in stored["recovery"]
                    ],
                    [
                        key
                        for key in ("hrv_rmssd_ms", "hrv_sdnn_ms", "hrv_ms")
                        if key in recovery
                    ],
                )
                self.assertEqual(
                    set(stored),
                    {"generated_at", "recovery", "_updated_at"},
                )

    def test_invalid_or_conflicting_hrv_payload_does_not_replace_snapshot(
        self,
    ) -> None:
        sentinel = {
            "generated_at": "2026-07-14T12:00:00Z",
            "recovery": {"date": "2026-07-14", "hrv_rmssd_ms": 51.0},
        }
        accepted = self.client.post(
            "/ingest", headers=self.authorization(), json=sentinel
        )
        self.assertEqual(accepted.status_code, 200)

        invalid_recoveries = [
            {
                "date": "2026-07-14",
                "hrv_rmssd_ms": 42.0,
                "hrv_sdnn_ms": 42.0,
            },
            {
                "date": "2026-07-14",
                "hrv_rmssd_ms": 42.0,
                "hrv_kind": "sdnn",
            },
            {"date": "2026-07-14", "hrv_ms": 0.0},
            {"date": "2026-07-14", "hrv_ms": "42"},
            {"date": "2026-07-14", "hrv_ms": 42.0, "hrv_sample_count": -1},
            {
                "date": "2026-07-14",
                "hrv_rmssd_ms": None,
                "hrv_contributed_to_recovery": True,
            },
            {"date": "2026-07-14", "hrv_sdnn_ms": 1_000.01},
            {"date": "2026-07-14", "hrv_rmssd": 42.0},
            {"date": "2026-07-14", "hrv_rmssd_ms": 42.0, "hrv_kind": "appleRMSSD"},
            {"date": "2026-07-14", "hrv_rmssd_ms": 42.0, "hrv_source": "appleRecoveryRMSSD"},
        ]

        for recovery in invalid_recoveries:
            with self.subTest(recovery=recovery):
                response = self.client.post(
                    "/ingest",
                    headers=self.authorization(),
                    json={
                        "generated_at": "2026-07-15T12:00:00Z",
                        "recovery": recovery,
                    },
                )
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(response.json(), {"detail": "Invalid request"})
                stored = self.store.get_snapshot()
                stored.pop("_updated_at")
                self.assertEqual(stored, sentinel)

    def test_snapshot_schema_drift_invalid_iso_and_nonfinite_values_fail_closed(
        self,
    ) -> None:
        sentinel = {
            "generated_at": "2026-07-14T12:00:00Z",
            "recovery": {"date": "2026-07-14", "hrv_rmssd_ms": 51.0},
        }
        self.assertEqual(
            self.client.post(
                "/ingest",
                headers=self.authorization(),
                json=sentinel,
            ).status_code,
            200,
        )

        invalid_snapshots = [
            {"generated_at": "2026-07-14T12:00:00"},
            {"generated_at": "not-a-timestamp"},
            {
                "generated_at": "2026-07-14T12:00:00Z",
                "future_top_level": True,
            },
            {
                "generated_at": "2026-07-14T12:00:00Z",
                "profile": {"goal": "kraft", "future_profile_field": True},
            },
            {
                "generated_at": "2026-07-14T12:00:00Z",
                "hrv_trend": [{"date": "2026-02-30", "sdnn_ms": 42.0}],
            },
            {
                "generated_at": "2026-07-14T12:00:00Z",
                "workouts": [
                    {
                        "date": "2026-07-14",
                        "type": "Run",
                        "duration_min": 30,
                    }
                ],
            },
            {
                "generated_at": "2026-07-14T12:00:00Z",
                "plans": [
                    {
                        "plan_id": str(uuid.uuid4()),
                        "base_plan_fingerprint": "sha256:" + "a" * 64,
                        "name": "Plan",
                        "active": True,
                        "source": "user",
                        "sessions": [],
                        "future_plan_field": True,
                    }
                ],
            },
        ]
        for snapshot in invalid_snapshots:
            with self.subTest(snapshot=snapshot):
                response = self.client.post(
                    "/ingest",
                    headers=self.authorization(),
                    json=snapshot,
                )
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(response.json(), {"detail": "Invalid request"})

        nonfinite = self.client.post(
            "/ingest",
            headers={
                **self.authorization(),
                "Content-Type": "application/json",
            },
            content=(
                b'{"generated_at":"2026-07-14T12:00:00Z",'
                b'"hrv_trend":[{"date":"2026-07-14","sdnn_ms":NaN}]}'
            ),
        )
        self.assertEqual(nonfinite.status_code, 422, nonfinite.text)
        self.assertEqual(nonfinite.json(), {"detail": "Invalid request"})
        stored = self.store.get_snapshot()
        stored.pop("_updated_at")
        self.assertEqual(stored, sentinel)

    def test_snapshot_collection_limit_rejects_without_replacing_last_snapshot(
        self,
    ) -> None:
        sentinel = {"generated_at": "2026-07-14T12:00:00Z"}
        self.assertEqual(
            self.client.post(
                "/ingest",
                headers=self.authorization(),
                json=sentinel,
            ).status_code,
            200,
        )
        overlimit = {
            "generated_at": "2026-07-15T12:00:00Z",
            "hrv_trend": [
                {"date": "2026-07-14", "sdnn_ms": 42.0}
                for _ in range(20_001)
            ],
        }

        response = self.client.post(
            "/ingest",
            headers=self.authorization(),
            json=overlimit,
        )

        self.assertEqual(response.status_code, 422, response.text)
        stored = self.store.get_snapshot()
        stored.pop("_updated_at")
        self.assertEqual(stored, sentinel)

    def test_plan_ack_requires_a_json_boolean(self) -> None:
        conversation_id = str(uuid.uuid4())
        origin = self.origin_ticket.VerifiedOrigin(
            version=1,
            conversation_id=conversation_id,
            principal="private-api",
            nonce=secrets.token_urlsafe(24),
            issued_at=int(time.time()),
            expires_at=int(time.time()) + 180,
        )
        plan_id = self.store.add_plan(
            {
                "schema_version": "1.0",
                "name": "Strict ACK",
                "sessions": [
                    {
                        "name": "Strength",
                        "exercises": [
                            {
                                "name": "Squat",
                                "sets": [{"weight_kg": 40, "reps": 8}],
                            }
                        ],
                    }
                ],
            },
            origin,
        )
        path = f"/plan/{plan_id}/ack"

        for invalid in ("true", "false", 1, 0, None):
            response = self.client.post(
                path,
                headers=self.authorization(),
                json={"accepted": invalid},
            )
            with self.subTest(invalid=invalid):
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(
                    [
                        plan["plan_id"]
                        for plan in self.store.pending_plans("private-api")
                        if plan["plan_id"] == plan_id
                    ],
                    [plan_id],
                )

        accepted = self.client.post(
            path,
            headers=self.authorization(),
            json={"accepted": False},
        )
        self.assertEqual(accepted.status_code, 200, accepted.text)

    def test_non_ascii_authorization_is_a_plain_401_not_a_server_error(self) -> None:
        # Header kommen als latin-1 an; ein str-Vergleich mit Nicht-ASCII warf in compare_digest.
        for value in (b"Bearer \xe9", b"Bearer " + self.token.encode()[:-1] + b"\xff"):
            with self.subTest(value=value):
                response = self.client.get("/plan/pending", headers={"Authorization": value})
                self.assertEqual(response.status_code, 401)
        self.assertEqual(
            self.client.get("/plan/pending", headers=self.authorization()).status_code, 200
        )

    def test_oversized_body_is_rejected_before_parsing(self) -> None:
        from body_limit import MAX_BODY_BYTES

        oversized = b"{" + b" " * MAX_BODY_BYTES + b"}"
        # With a valid token the ceiling answers. Without one, the bearer gate answers 401
        # first (0.13 S11b): an unauthenticated sender never gets as far as the size check.
        response = self.client.post(
            "/ingest",
            content=oversized,
            headers={"Content-Type": "application/json", **self.authorization()},
        )
        self.assertEqual(response.status_code, 413)
        response = self.client.post(
            "/ingest", content=oversized, headers={"Content-Type": "application/json"}
        )
        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
