"""Snapshot v2 (contract/snapshot-v2.md): daily steps, body and nutrition lists — model and store.

All values are synthetic.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from pydantic import ValidationError


SERVER_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_ROOT))

from models import Snapshot  # noqa: E402
from store import KoreStore  # noqa: E402


GENERATED_AT = "2026-07-14T12:00:00Z"
TREND_KEYS = ("steps", "body", "nutrition")


def day(offset: int) -> str:
    return (date(2026, 6, 1) + timedelta(days=offset)).isoformat()


def steps(count: int = 3, value: int = 8_000) -> list[dict[str, object]]:
    return [{"date": day(index), "count": value + index} for index in range(count)]


def body(count: int = 3) -> list[dict[str, object]]:
    return [
        {"date": day(index), "weight_kg": 80.5 - index / 10, "body_fat_pct": 18.0}
        for index in range(count)
    ]


def nutrition(count: int = 3) -> list[dict[str, object]]:
    return [
        {"date": day(index), "kcal": 2_400.0, "protein_g": 150.0, "carbs_g": 260.0, "fat_g": 80.0}
        for index in range(count)
    ]


def snapshot(**lists: object) -> dict[str, object]:
    return {"generated_at": GENERATED_AT, **lists}


def dumped(payload: dict[str, object]) -> dict[str, object]:
    return Snapshot.model_validate(payload).model_dump(mode="json", exclude_unset=True)


class SnapshotV2ModelTests(unittest.TestCase):
    def assert_rejected(self, payload: dict[str, object]) -> None:
        with self.assertRaises(ValidationError):
            Snapshot.model_validate(payload)

    def test_all_three_lists_round_trip_without_added_fields(self) -> None:
        payload = snapshot(
            steps=steps(),
            body=[
                {"date": day(0), "weight_kg": 80.5},
                {"date": day(1), "weight_kg": 80.25, "body_fat_pct": 18.5},
            ],
            nutrition=[
                {"date": day(0), "kcal": 2_100.0},
                {"date": day(1), "protein_g": 140.0, "fat_g": 70.5},
                {"date": day(2), "kcal": 2_400.0, "protein_g": 150.0, "carbs_g": 260.0, "fat_g": 80.0},
            ],
        )
        self.assertEqual(dumped(payload), payload)

    def test_lists_are_optional_and_may_be_empty(self) -> None:
        self.assertEqual(dumped(snapshot()), snapshot())
        empty = snapshot(steps=[], body=[], nutrition=[])
        self.assertEqual(dumped(empty), empty)

    def test_whole_numbers_are_accepted_for_kilograms_percent_and_grams(self) -> None:
        # JSONSerialization writes 80.0 as 80; that is still a valid weight.
        payload = snapshot(
            body=[{"date": day(0), "weight_kg": 80, "body_fat_pct": 18}],
            nutrition=[{"date": day(0), "kcal": 2_000, "protein_g": 150, "carbs_g": 0, "fat_g": 70}],
        )
        result = dumped(payload)
        self.assertEqual(result["body"], [{"date": day(0), "weight_kg": 80.0, "body_fat_pct": 18.0}])
        self.assertEqual(result["nutrition"][0]["kcal"], 2_000.0)

    def test_boundaries_are_inclusive(self) -> None:
        payload = snapshot(
            steps=[{"date": day(0), "count": 0}, {"date": day(1), "count": 200_000}],
            body=[
                {"date": day(0), "weight_kg": 20.0, "body_fat_pct": 1.0},
                {"date": day(1), "weight_kg": 400.0, "body_fat_pct": 75.0},
            ],
            nutrition=[
                {"date": day(0), "kcal": 0.0, "protein_g": 0.0, "carbs_g": 0.0, "fat_g": 0.0},
                {
                    "date": day(1), "kcal": 20_000.0,
                    "protein_g": 2_000.0, "carbs_g": 2_000.0, "fat_g": 2_000.0,
                },
            ],
        )
        self.assertEqual(dumped(payload), payload)

    def test_values_outside_the_ranges_are_rejected(self) -> None:
        cases = {
            "negative steps": {"steps": [{"date": day(0), "count": -1}]},
            "too many steps": {"steps": [{"date": day(0), "count": 200_001}]},
            "weight too small": {"body": [{"date": day(0), "weight_kg": 19.9}]},
            "weight too large": {"body": [{"date": day(0), "weight_kg": 400.1}]},
            "negative weight": {"body": [{"date": day(0), "weight_kg": -80.0}]},
            "body fat too small": {"body": [{"date": day(0), "weight_kg": 80.0, "body_fat_pct": 0.9}]},
            # HealthKit stores body fat as a fraction; the snapshot carries percent.
            "body fat as fraction": {"body": [{"date": day(0), "weight_kg": 80.0, "body_fat_pct": 0.18}]},
            "body fat too large": {"body": [{"date": day(0), "weight_kg": 80.0, "body_fat_pct": 75.1}]},
            "negative kcal": {"nutrition": [{"date": day(0), "kcal": -1.0}]},
            "too many kcal": {"nutrition": [{"date": day(0), "kcal": 20_000.1}]},
            "negative protein": {"nutrition": [{"date": day(0), "protein_g": -0.1}]},
            "too much carbs": {"nutrition": [{"date": day(0), "carbs_g": 2_000.1}]},
            "too much fat": {"nutrition": [{"date": day(0), "fat_g": 2_000.1}]},
        }
        for name, lists in cases.items():
            with self.subTest(name):
                self.assert_rejected(snapshot(**lists))

    def test_nan_and_infinity_are_rejected(self) -> None:
        for value in (float("nan"), float("inf"), float("-inf")):
            cases = (
                {"body": [{"date": day(0), "weight_kg": value}]},
                {"body": [{"date": day(0), "weight_kg": 80.0, "body_fat_pct": value}]},
                {"nutrition": [{"date": day(0), "kcal": value}]},
                {"nutrition": [{"date": day(0), "fat_g": value}]},
            )
            for lists in cases:
                with self.subTest(value=value, lists=lists):
                    self.assert_rejected(snapshot(**lists))

    def test_types_are_strict(self) -> None:
        cases = (
            {"steps": [{"date": day(0), "count": 8_000.0}]},
            {"steps": [{"date": day(0), "count": 8_000.5}]},
            {"steps": [{"date": day(0), "count": "8000"}]},
            {"steps": [{"date": day(0), "count": True}]},
            {"body": [{"date": day(0), "weight_kg": "80.5"}]},
            {"body": [{"date": day(0), "weight_kg": True}]},
            {"nutrition": [{"date": day(0), "kcal": "2000"}]},
            {"nutrition": [{"date": day(0), "protein_g": False}]},
            {"steps": {"date": day(0), "count": 1}},
            {"steps": "8000"},
        )
        for lists in cases:
            with self.subTest(lists=lists):
                self.assert_rejected(snapshot(**lists))

    def test_null_is_never_a_value_leave_the_key_out_instead(self) -> None:
        cases = (
            {"steps": None},
            {"body": None},
            {"nutrition": None},
            {"steps": [{"date": day(0), "count": None}]},
            {"body": [{"date": day(0), "weight_kg": 80.0, "body_fat_pct": None}]},
            {"nutrition": [{"date": day(0), "kcal": 2_000.0, "fat_g": None}]},
            {"nutrition": [{"date": day(0), "kcal": None}]},
        )
        for lists in cases:
            with self.subTest(lists=lists):
                self.assert_rejected(snapshot(**lists))

    def test_dates_are_canonical_local_calendar_days(self) -> None:
        for value in ("2026-6-1", "2026-06-01T00:00:00Z", "2026-02-30", "01.06.2026", "", 20260601, None):
            with self.subTest(value=value):
                self.assert_rejected(snapshot(steps=[{"date": value, "count": 1}]))

    def test_required_fields_and_unknown_fields(self) -> None:
        cases = (
            {"steps": [{"date": day(0)}]},
            {"steps": [{"count": 1}]},
            {"body": [{"date": day(0), "body_fat_pct": 18.0}]},
            {"nutrition": [{"kcal": 2_000.0}]},
            {"steps": [{"date": day(0), "count": 1, "distance_km": 5.0}]},
            {"body": [{"date": day(0), "weight_kg": 80.0, "lean_mass_kg": 60.0}]},
            {"nutrition": [{"date": day(0), "kcal": 2_000.0, "sugar_g": 50.0}]},
            {"water_ml": [{"date": day(0), "value": 2_000.0}]},
        )
        for lists in cases:
            with self.subTest(lists=lists):
                self.assert_rejected(snapshot(**lists))

    def test_a_nutrition_day_needs_at_least_one_value(self) -> None:
        self.assert_rejected(snapshot(nutrition=[{"date": day(0)}]))
        for key in ("kcal", "protein_g", "carbs_g", "fat_g"):
            with self.subTest(key=key):
                payload = snapshot(nutrition=[{"date": day(0), key: 0.0}])
                self.assertEqual(dumped(payload), payload)

    def test_each_list_holds_at_most_31_days(self) -> None:
        builders = {"steps": steps, "body": body, "nutrition": nutrition}
        for key, build in builders.items():
            with self.subTest(key=key):
                self.assertEqual(len(dumped(snapshot(**{key: build(31)}))[key]), 31)
                self.assert_rejected(snapshot(**{key: build(32)}))

    def test_a_date_appears_at_most_once_per_list(self) -> None:
        cases = {
            "steps": [{"date": day(0), "count": 1}, {"date": day(0), "count": 2}],
            "body": [{"date": day(0), "weight_kg": 80.0}, {"date": day(0), "weight_kg": 81.0}],
            "nutrition": [{"date": day(0), "kcal": 1.0}, {"date": day(0), "fat_g": 2.0}],
        }
        for key, entries in cases.items():
            with self.subTest(key=key):
                self.assert_rejected(snapshot(**{key: entries}))
        # The same day in different lists is the normal case.
        same_day = snapshot(steps=steps(1), body=body(1), nutrition=nutrition(1))
        self.assertEqual(dumped(same_day), same_day)

    def test_the_lists_count_towards_the_transport_limit(self) -> None:
        hrv = [{"date": day(index % 28), "sdnn_ms": 42.0} for index in range(19_999)]
        self.assertEqual(len(dumped(snapshot(hrv_trend=hrv, steps=steps(1)))["steps"]), 1)
        self.assert_rejected(snapshot(hrv_trend=hrv, steps=steps(1), body=body(1)))


class SnapshotV2StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.store = KoreStore(
            Path(self.temp_dir.name) / "snapshot-v2.db",
            instance_id="snapshot-v2",
            principal="snapshot-v2",
        )
        self.store.init()

    def stored(self) -> dict[str, object]:
        value = self.store.get_snapshot()
        value.pop("_updated_at", None)
        return value

    def test_a_list_in_the_snapshot_replaces_the_stored_one(self) -> None:
        self.store.save_snapshot(snapshot(steps=steps(3), body=body(3), nutrition=nutrition(3)))
        replacement = snapshot(steps=steps(2, value=9_000), body=body(1), nutrition=nutrition(2))
        self.store.save_snapshot(replacement)
        self.assertEqual(self.stored(), replacement)

    def test_a_missing_list_keeps_the_stored_one(self) -> None:
        self.store.save_snapshot(
            snapshot(steps=steps(), body=body(), nutrition=nutrition(), sleep=[], active_plan_name="A")
        )
        # A snapshot of an app without snapshot v2 (or of a sync against an older capability set).
        older_app = {"generated_at": "2026-07-15T12:00:00Z", "hrv_trend": [{"date": day(0), "sdnn_ms": 42.0}]}
        self.store.save_snapshot(older_app)
        self.assertEqual(
            self.stored(),
            {**older_app, "steps": steps(), "body": body(), "nutrition": nutrition()},
        )

    def test_each_list_is_kept_or_replaced_on_its_own(self) -> None:
        self.store.save_snapshot(snapshot(steps=steps(), body=body(), nutrition=nutrition()))
        self.store.save_snapshot(snapshot(nutrition=[]))
        self.assertEqual(self.stored(), snapshot(steps=steps(), body=body(), nutrition=[]))

    def test_an_empty_list_clears_and_stays_cleared(self) -> None:
        self.store.save_snapshot(snapshot(steps=steps(), body=body(), nutrition=nutrition()))
        self.store.save_snapshot(snapshot(steps=[], body=[], nutrition=[]))
        self.assertEqual(self.stored(), snapshot(steps=[], body=[], nutrition=[]))
        self.store.save_snapshot(snapshot())
        self.assertEqual(self.stored(), snapshot(steps=[], body=[], nutrition=[]))

    def test_nothing_is_invented_without_a_stored_list(self) -> None:
        self.store.save_snapshot(snapshot())
        self.assertEqual(self.stored(), snapshot())
        self.store.save_snapshot(snapshot(steps=steps(1)))
        self.store.save_snapshot(snapshot())
        self.assertEqual(self.stored(), snapshot(steps=steps(1)))

    def test_the_lists_are_kept_in_every_workout_update_mode(self) -> None:
        local = {
            "id": "local-1", "source": "local", "date": GENERATED_AT,
            "type": "Strength", "duration_min": 30,
        }
        self.store.save_snapshot(
            snapshot(steps=steps(), body=body(), nutrition=nutrition(), workouts=[local])
        )
        self.store.save_snapshot(snapshot(), workout_update="preserve")
        self.assertEqual(
            self.stored(),
            snapshot(steps=steps(), body=body(), nutrition=nutrition(), workouts=[local]),
        )
        self.store.save_snapshot(snapshot(workouts=[]), workout_update="replace_local")
        self.assertEqual(
            self.stored(),
            snapshot(steps=steps(), body=body(), nutrition=nutrition(), workouts=[]),
        )

    def test_the_final_validation_sees_the_kept_lists(self) -> None:
        self.store.save_snapshot(snapshot(steps=steps(), body=body(), nutrition=nutrition()))
        seen: list[dict[str, object]] = []
        self.store.save_snapshot(snapshot(), validate_final_payload=seen.append)
        self.assertEqual(seen, [snapshot(steps=steps(), body=body(), nutrition=nutrition())])


if __name__ == "__main__":
    unittest.main()
