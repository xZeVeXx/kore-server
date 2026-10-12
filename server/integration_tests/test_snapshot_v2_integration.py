"""Snapshot v2 against the real ingest app (contract/snapshot-v2.md): `GET /capabilities` and the
steps/body/nutrition lists. All values are synthetic."""

from __future__ import annotations

import importlib
import os
import secrets
import sqlite3
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from fastapi.testclient import TestClient


SERVER_ROOT = Path(__file__).resolve().parents[1]
BRIDGE_ROOT = SERVER_ROOT.parent / "coach-bridge"
sys.path.insert(0, str(SERVER_ROOT))

_FRESH_MODULES = ("ingest_api", "store", "models", "origin_ticket", "auth_devices", "auth_gate")
_ENV_KEYS = (
    "KORE_DB_PATH", "KORE_INSTANCE_ID", "KORE_INGEST_TOKEN",
    "KORE_PLAN_ORIGIN_PRINCIPAL", "KORE_STATE_DIR",
)
CAPABILITIES = {"snapshot_capabilities": ["activity_body_nutrition_v1", "apple_hrv_v1"]}


def _load_pairing():
    sys.path.insert(0, str(BRIDGE_ROOT))
    try:
        return importlib.import_module("pairing")
    finally:
        sys.path.remove(str(BRIDGE_ROOT))


def day(offset: int) -> str:
    return (date(2026, 6, 1) + timedelta(days=offset)).isoformat()


def steps(count: int = 3, value: int = 8_000) -> list[dict[str, object]]:
    return [{"date": day(index), "count": value + index} for index in range(count)]


def body(count: int = 3) -> list[dict[str, object]]:
    return [{"date": day(index), "weight_kg": 80.5, "body_fat_pct": 18.0} for index in range(count)]


def nutrition(count: int = 3) -> list[dict[str, object]]:
    return [
        {"date": day(index), "kcal": 2_400.0, "protein_g": 150.0, "carbs_g": 260.0, "fat_g": 80.0}
        for index in range(count)
    ]


# What app 0.13 sends: no steps, body or nutrition.
APP_0_13_SNAPSHOT = {
    "generated_at": "2026-07-15T12:00:00Z",
    "profile": {"age": 35, "goal": "kraft", "units": "kg"},
    "recovery": {"date": "2026-07-15", "readiness": 81, "hrv_ms": 61.0, "resting_hr": 52},
    "hrv_trend": [{"date": "2026-07-14", "sdnn_ms": 49.0}],
    "sleep": [{"date": "2026-07-14", "total_h": 7.5}],
    "workouts": [],
}


class SnapshotV2IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.state_dir = Path(cls.temp_dir.name) / "state"
        cls.db_path = Path(cls.temp_dir.name) / "snapshot-v2.db"
        cls.token = secrets.token_urlsafe(32)
        cls._saved_env = {key: os.environ.get(key) for key in _ENV_KEYS}
        os.environ.update({
            "KORE_DB_PATH": str(cls.db_path),
            "KORE_INSTANCE_ID": "snapshot-v2",
            "KORE_INGEST_TOKEN": cls.token,
            "KORE_PLAN_ORIGIN_PRINCIPAL": "snapshot-v2",
            "KORE_STATE_DIR": str(cls.state_dir),
        })
        for name in _FRESH_MODULES:
            sys.modules.pop(name, None)
        cls.ingest_api = importlib.import_module("ingest_api")
        cls.store = importlib.import_module("store")
        cls.pairing = _load_pairing()
        cls.client_context = TestClient(cls.ingest_api.app)
        cls.client = cls.client_context.__enter__()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client_context.__exit__(None, None, None)
        for key, value in cls._saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        for name in _FRESH_MODULES:
            sys.modules.pop(name, None)
        cls.temp_dir.cleanup()

    def setUp(self) -> None:
        # Every test starts without a stored snapshot: kept lists must not leak between tests.
        with sqlite3.connect(self.db_path) as db:
            db.execute("DELETE FROM snapshot")

    def authorization(self, token: str | None = None) -> dict[str, str]:
        return {"Authorization": f"Bearer {token or self.token}"}

    def ingest(self, payload: dict[str, object], token: str | None = None):
        return self.client.post("/ingest", headers=self.authorization(token), json=payload)

    def stored(self) -> dict[str, object]:
        value = self.store.get_snapshot()
        value.pop("_updated_at", None)
        return value

    # -- GET /capabilities ---------------------------------------------------------------------

    def test_capabilities_with_the_static_token(self) -> None:
        response = self.client.get("/capabilities", headers=self.authorization())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), CAPABILITIES)
        names = response.json()["snapshot_capabilities"]
        self.assertEqual(names, sorted(names))

    def test_capabilities_with_a_paired_device_token_until_revoked(self) -> None:
        pairing_store = self.pairing.PairingStore(str(self.state_dir))
        device = pairing_store.redeem(pairing_store.create_code()["code"], "Test phone", "10.0.0.10")
        response = self.client.get("/capabilities", headers=self.authorization(device["token"]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), CAPABILITIES)
        self.assertTrue(pairing_store.revoke(device["device_id"]))
        revoked = self.client.get("/capabilities", headers=self.authorization(device["token"]))
        self.assertEqual(revoked.status_code, 401)

    def test_capabilities_without_or_with_a_wrong_token_is_401(self) -> None:
        for headers in ({}, {"Authorization": "Bearer " + "x" * 43}, {"Authorization": self.token}):
            with self.subTest(headers=bool(headers)):
                response = self.client.get("/capabilities", headers=headers)
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.json(), {"detail": "Unauthorized"})
        self.assertNotIn("/capabilities", self.ingest_api.ANONYMOUS_PATHS)

    def test_health_stays_minimal(self) -> None:
        self.assertEqual(self.ingest_api.ANONYMOUS_PATHS, frozenset({"/health"}))
        for headers in ({}, self.authorization()):
            with self.subTest(authenticated=bool(headers)):
                response = self.client.get("/health", headers=headers)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), {"ok": True})

    # -- lists in POST /ingest -----------------------------------------------------------------

    def test_lists_are_stored_as_sent_and_a_new_list_replaces_the_old_one(self) -> None:
        first = {**APP_0_13_SNAPSHOT, "steps": steps(), "body": body(), "nutrition": nutrition()}
        self.assertEqual(self.ingest(first).status_code, 200)
        self.assertEqual(self.stored(), first)

        second = {
            **APP_0_13_SNAPSHOT,
            "generated_at": "2026-07-16T12:00:00Z",
            "steps": steps(31, value=9_000),
            "body": [{"date": day(2), "weight_kg": 79.5}],
            "nutrition": [{"date": day(2), "kcal": 1_900.0}],
        }
        response = self.ingest(second)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"ok": True})
        self.assertEqual(self.stored(), second)

    def test_a_snapshot_of_app_0_13_is_accepted_and_keeps_the_stored_lists(self) -> None:
        lists = {"steps": steps(), "body": body(), "nutrition": nutrition()}
        self.assertEqual(self.ingest({**APP_0_13_SNAPSHOT, **lists}).status_code, 200)

        response = self.ingest(APP_0_13_SNAPSHOT)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.stored(), {**APP_0_13_SNAPSHOT, **lists})

        # The same with the workout scope an app sends while Health workouts are unconfirmed.
        unconfirmed = {
            key: value for key, value in APP_0_13_SNAPSHOT.items() if key != "workouts"
        }
        unconfirmed.update(generated_at="2026-07-16T12:00:00Z", workouts_scope="unconfirmed")
        response = self.ingest(unconfirmed)
        self.assertEqual(response.status_code, 200, response.text)
        stored = self.stored()
        self.assertEqual(stored["generated_at"], "2026-07-16T12:00:00Z")
        self.assertEqual({key: stored[key] for key in lists}, lists)

    def test_a_device_token_sync_can_carry_the_lists(self) -> None:
        pairing_store = self.pairing.PairingStore(str(self.state_dir))
        device = pairing_store.redeem(pairing_store.create_code()["code"], "Test phone", "10.0.0.10")
        payload = {**APP_0_13_SNAPSHOT, "steps": steps(), "body": [], "nutrition": []}
        self.assertEqual(self.ingest(payload, token=device["token"]).status_code, 200)
        self.assertEqual(self.stored(), payload)

    def test_empty_lists_clear_and_a_later_snapshot_without_lists_keeps_them_empty(self) -> None:
        full = {**APP_0_13_SNAPSHOT, "steps": steps(), "body": body(), "nutrition": nutrition()}
        self.assertEqual(self.ingest(full).status_code, 200)
        # Nutrition switched off in the app: the next sync sends an empty nutrition list.
        switched_off = {**APP_0_13_SNAPSHOT, "steps": steps(), "body": body(), "nutrition": []}
        self.assertEqual(self.ingest(switched_off).status_code, 200)
        self.assertEqual(self.stored()["nutrition"], [])
        self.assertEqual(self.stored()["steps"], steps())

        self.assertEqual(self.ingest(APP_0_13_SNAPSHOT).status_code, 200)
        self.assertEqual(self.stored()["nutrition"], [])

        cleared = {**APP_0_13_SNAPSHOT, "steps": [], "body": [], "nutrition": []}
        self.assertEqual(self.ingest(cleared).status_code, 200)
        self.assertEqual(self.stored(), cleared)

    def test_invalid_lists_are_422_and_leave_the_stored_snapshot_unchanged(self) -> None:
        sentinel = {**APP_0_13_SNAPSHOT, "steps": steps(), "body": body(), "nutrition": nutrition()}
        self.assertEqual(self.ingest(sentinel).status_code, 200)

        invalid = {
            "32 steps": {"steps": steps(32)},
            "32 body entries": {"body": body(32)},
            "32 nutrition days": {"nutrition": nutrition(32)},
            "duplicate step day": {
                "steps": [{"date": day(0), "count": 1}, {"date": day(0), "count": 2}],
            },
            "duplicate body day": {"body": [{"date": day(0), "weight_kg": 80.0}] * 2},
            "duplicate nutrition day": {"nutrition": [{"date": day(0), "kcal": 1.0}] * 2},
            "negative steps": {"steps": [{"date": day(0), "count": -1}]},
            "too many steps": {"steps": [{"date": day(0), "count": 200_001}]},
            "weight too small": {"body": [{"date": day(0), "weight_kg": 19.0}]},
            "weight too large": {"body": [{"date": day(0), "weight_kg": 401.0}]},
            "body fat as fraction": {
                "body": [{"date": day(0), "weight_kg": 80.0, "body_fat_pct": 0.18}],
            },
            "body fat too large": {
                "body": [{"date": day(0), "weight_kg": 80.0, "body_fat_pct": 76.0}],
            },
            "negative kcal": {"nutrition": [{"date": day(0), "kcal": -5.0}]},
            "too many kcal": {"nutrition": [{"date": day(0), "kcal": 20_001.0}]},
            "too much protein": {"nutrition": [{"date": day(0), "protein_g": 2_001.0}]},
            "empty nutrition day": {"nutrition": [{"date": day(0)}]},
            "unknown field": {"steps": [{"date": day(0), "count": 1, "floors": 3}]},
            "null list": {"nutrition": None},
            "string count": {"steps": [{"date": day(0), "count": "8000"}]},
            "boolean weight": {"body": [{"date": day(0), "weight_kg": True}]},
            "non-canonical date": {"steps": [{"date": "2026-6-1", "count": 1}]},
        }
        for name, lists in invalid.items():
            with self.subTest(name):
                response = self.ingest(
                    {**APP_0_13_SNAPSHOT, "generated_at": "2026-07-16T12:00:00Z", **lists}
                )
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(response.json(), {"detail": "Invalid request"})
                self.assertEqual(self.stored(), sentinel)

        for literal in (b"NaN", b"Infinity", b"-Infinity"):
            for field in (b'"body":[{"date":"2026-06-01","weight_kg":%s}]',
                          b'"nutrition":[{"date":"2026-06-01","kcal":%s}]'):
                with self.subTest(literal=literal, field=field):
                    response = self.client.post(
                        "/ingest",
                        headers={**self.authorization(), "Content-Type": "application/json"},
                        content=b'{"generated_at":"2026-07-16T12:00:00Z",' + field % literal + b"}",
                    )
                    self.assertEqual(response.status_code, 422, response.text)
                    self.assertEqual(response.json(), {"detail": "Invalid request"})
                    self.assertEqual(self.stored(), sentinel)

    def test_the_rejection_does_not_echo_the_values(self) -> None:
        response = self.ingest(
            {"generated_at": "2026-07-16T12:00:00Z", "body": [{"date": day(0), "weight_kg": 401.25}]}
        )
        self.assertEqual(response.status_code, 422)
        self.assertNotIn("401.25", response.text)


if __name__ == "__main__":
    unittest.main()
