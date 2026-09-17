from __future__ import annotations

import copy
import json
import math
import secrets
import sqlite3
import sys
import tempfile
import unittest
import uuid
from pathlib import Path


SERVER_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_ROOT))

from origin_ticket import VerifiedOrigin  # noqa: E402
from plan_contract import (  # noqa: E402
    InvalidPlanProposal,
    MAXIMUM_PENDING_RESPONSE_BYTES,
    MAXIMUM_REPETITIONS,
    MAXIMUM_REST_SECONDS,
    MAXIMUM_WARMUP_SETS,
    MAXIMUM_WARMUP_WEIGHT_KG,
    validate_and_normalize_plan,
)
from store import KoreStore  # noqa: E402


SESSION_ID = "3f84835e-35f4-4f99-bfc8-3ee3c7b2f64f"
EXERCISE_ID = "c235e138-4e06-4e50-a654-c48463f1b821"
CONVERSATION_ID = "70f84143-a6b6-49e0-a2c8-08ea57bcf843"
PLAN_FINGERPRINT = "sha256:" + "a" * 64


def strength_plan() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "name": "Strength A",
        "rationale": "Recovery is stable.",
        "sessions": [
            {
                "session_id": SESSION_ID,
                "name": "Push",
                "kind": "strength",
                "cardio_target": None,
                "exercises": [
                    {
                        "name": "Bench Press",
                        "note": "Barbell",
                        "sets": [{"weight_kg": 60, "reps": 8}],
                    }
                ],
            }
        ],
    }


def declare_update(
    plan: dict[str, object],
    *,
    replacement_id: str | None = None,
    replacement_name: str | None = None,
) -> dict[str, object]:
    if replacement_id is not None:
        plan["replaces_plan_id"] = replacement_id
    if replacement_name is not None:
        plan["replaces_plan"] = replacement_name
    plan["base_plan_fingerprint"] = PLAN_FINGERPRINT
    plan["removed_session_ids"] = []
    plan["removed_exercise_ids"] = []
    return plan


def cardio_plan() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "name": "Cardio A",
        "sessions": [
            {
                "name": "Run",
                "kind": "cardio",
                "cardio_target": {"duration_min": 30, "distance_km": 5},
                "exercises": [],
            }
        ],
    }


class PlanContractTests(unittest.TestCase):
    def assertInvalid(self, plan: dict[str, object]) -> None:
        with self.assertRaises(InvalidPlanProposal):
            validate_and_normalize_plan(plan)

    def test_strength_output_is_bounded_canonical_and_has_no_nulls(self) -> None:
        plan = strength_plan()
        plan["sessions"][0]["session_id"] = SESSION_ID.upper()

        normalized = validate_and_normalize_plan(plan)

        self.assertEqual(normalized["schema_version"], "1.0")
        self.assertEqual(normalized["sessions"][0]["session_id"], SESSION_ID)
        self.assertEqual(normalized["sessions"][0]["kind"], "strength")
        self.assertNotIn("cardio_target", normalized["sessions"][0])
        self.assertNotIn("replaces_plan", normalized)

    def test_legacy_strength_kind_and_session_id_remain_optional(self) -> None:
        plan = strength_plan()
        del plan["sessions"][0]["kind"]
        del plan["sessions"][0]["session_id"]

        normalized = validate_and_normalize_plan(plan)

        self.assertNotIn("kind", normalized["sessions"][0])
        self.assertNotIn("session_id", normalized["sessions"][0])

    def test_replacement_plan_id_is_canonical_optional_and_non_null(self) -> None:
        replacement_id = "5D2584DC-95DE-4F28-B0D0-755FE76733A9"
        plan = declare_update(strength_plan(), replacement_id=replacement_id)
        normalized = validate_and_normalize_plan(plan)
        self.assertEqual(
            normalized["replaces_plan_id"],
            replacement_id.lower(),
        )

        legacy = validate_and_normalize_plan(strength_plan())
        self.assertNotIn("replaces_plan_id", legacy)

        for value in (None, "not-a-uuid", 123):
            invalid = strength_plan()
            invalid["replaces_plan_id"] = value
            with self.subTest(value=value):
                self.assertInvalid(invalid)

    def test_update_contract_requires_fingerprint_and_explicit_unique_removals(self) -> None:
        replacement_id = str(uuid.uuid4())
        normalized = validate_and_normalize_plan(
            declare_update(
                strength_plan(),
                replacement_id=replacement_id,
                replacement_name="Strength A",
            )
        )
        self.assertEqual(normalized["base_plan_fingerprint"], PLAN_FINGERPRINT)
        self.assertEqual(normalized["removed_session_ids"], [])
        self.assertEqual(normalized["removed_exercise_ids"], [])

        for missing in (
            "base_plan_fingerprint",
            "removed_session_ids",
            "removed_exercise_ids",
        ):
            invalid = declare_update(strength_plan(), replacement_id=replacement_id)
            del invalid[missing]
            with self.subTest(missing=missing):
                self.assertInvalid(invalid)

        duplicate = declare_update(strength_plan(), replacement_id=replacement_id)
        duplicate["removed_session_ids"] = [SESSION_ID, SESSION_ID.upper()]
        self.assertInvalid(duplicate)

        malformed = declare_update(strength_plan(), replacement_id=replacement_id)
        malformed["base_plan_fingerprint"] = "sha256:" + "A" * 64
        self.assertInvalid(malformed)

    def test_new_plans_reject_update_only_fields_and_valid_from(self) -> None:
        for key, value in (
            ("base_plan_fingerprint", PLAN_FINGERPRINT),
            ("removed_session_ids", []),
            ("removed_exercise_ids", []),
            ("valid_from", "2026-07-14"),
        ):
            invalid = strength_plan()
            invalid[key] = value
            with self.subTest(key=key):
                self.assertInvalid(invalid)

    def test_proposal_cannot_overload_server_plan_or_origin_identity(self) -> None:
        for key, value in (
            ("plan_id", str(uuid.uuid4())),
            (
                "origin",
                {"version": 1, "conversation_id": CONVERSATION_ID},
            ),
        ):
            invalid = strength_plan()
            invalid[key] = value
            with self.subTest(key=key):
                self.assertInvalid(invalid)

    def test_advanced_exercise_fields_are_lossless_and_presence_sensitive(self) -> None:
        plan = strength_plan()
        exercise = plan["sessions"][0]["exercises"][0]
        exercise.update(
            {
                "exercise_id": EXERCISE_ID.upper(),
                "rest_seconds": 0,
                "warmup_sets": 0,
                "warmup_weight_kg": 0,
                "bilateral": False,
            }
        )
        exercise["sets"][0]["reps_max"] = 12

        normalized = validate_and_normalize_plan(plan)
        advanced = normalized["sessions"][0]["exercises"][0]

        self.assertEqual(advanced["exercise_id"], EXERCISE_ID)
        self.assertEqual(advanced["sets"][0]["reps_max"], 12)
        self.assertEqual(advanced["rest_seconds"], 0)
        self.assertEqual(advanced["warmup_sets"], 0)
        self.assertEqual(advanced["warmup_weight_kg"], 0.0)
        self.assertIs(advanced["bilateral"], False)

        legacy = validate_and_normalize_plan(strength_plan())
        legacy_exercise = legacy["sessions"][0]["exercises"][0]
        for key in (
            "exercise_id",
            "rest_seconds",
            "warmup_sets",
            "warmup_weight_kg",
            "bilateral",
        ):
            self.assertNotIn(key, legacy_exercise)
        self.assertNotIn("reps_max", legacy_exercise["sets"][0])

    def test_explicit_null_advanced_fields_are_not_treated_as_omission(self) -> None:
        mutations: list[dict[str, object]] = []
        for key in (
            "exercise_id",
            "rest_seconds",
            "warmup_sets",
            "warmup_weight_kg",
            "bilateral",
        ):
            plan = strength_plan()
            plan["sessions"][0]["exercises"][0][key] = None
            mutations.append(plan)
        null_reps_max = strength_plan()
        null_reps_max["sessions"][0]["exercises"][0]["sets"][0][
            "reps_max"
        ] = None
        mutations.append(null_reps_max)

        for plan in mutations:
            with self.subTest(plan=plan):
                self.assertInvalid(plan)

    def test_unknown_advanced_fields_fail_instead_of_being_discarded(self) -> None:
        unknown_exercise = strength_plan()
        unknown_exercise["sessions"][0]["exercises"][0][
            "tempo_seconds"
        ] = 3
        unknown_set = strength_plan()
        unknown_set["sessions"][0]["exercises"][0]["sets"][0]["rir"] = 2
        camel_case_alias = strength_plan()
        camel_case_alias["sessions"][0]["exercises"][0][
            "restSeconds"
        ] = 120

        for plan in (unknown_exercise, unknown_set, camel_case_alias):
            self.assertInvalid(plan)

    def test_advanced_field_bounds_match_the_swift_proposal_contract(self) -> None:
        plan = strength_plan()
        exercise = plan["sessions"][0]["exercises"][0]
        exercise.update(
            {
                "rest_seconds": MAXIMUM_REST_SECONDS,
                "warmup_sets": MAXIMUM_WARMUP_SETS,
                "warmup_weight_kg": MAXIMUM_WARMUP_WEIGHT_KG,
                "bilateral": True,
            }
        )
        exercise["sets"][0]["reps"] = MAXIMUM_REPETITIONS - 1
        exercise["sets"][0]["reps_max"] = MAXIMUM_REPETITIONS
        normalized = validate_and_normalize_plan(plan)
        self.assertEqual(
            normalized["sessions"][0]["exercises"][0]["sets"][0][
                "reps_max"
            ],
            MAXIMUM_REPETITIONS,
        )

        invalid_values = (
            ("rest_seconds", MAXIMUM_REST_SECONDS + 1),
            ("rest_seconds", -1),
            ("warmup_sets", MAXIMUM_WARMUP_SETS + 1),
            ("warmup_sets", 1.5),
            ("warmup_weight_kg", MAXIMUM_WARMUP_WEIGHT_KG + 0.01),
            ("warmup_weight_kg", math.inf),
            ("bilateral", 1),
        )
        for key, value in invalid_values:
            invalid = strength_plan()
            invalid["sessions"][0]["exercises"][0][key] = value
            with self.subTest(key=key, value=value):
                self.assertInvalid(invalid)

        for value in (8, 7, MAXIMUM_REPETITIONS + 1, 8.5, None):
            invalid = strength_plan()
            invalid["sessions"][0]["exercises"][0]["sets"][0][
                "reps_max"
            ] = value
            with self.subTest(reps_max=value):
                self.assertInvalid(invalid)

    def test_cardio_accepts_no_exercises_and_omits_null_target(self) -> None:
        plan = cardio_plan()
        plan["sessions"][0]["cardio_target"] = None

        normalized = validate_and_normalize_plan(plan)

        self.assertEqual(normalized["sessions"][0]["exercises"], [])
        self.assertNotIn("cardio_target", normalized["sessions"][0])

        explicit_free = cardio_plan()
        explicit_free["sessions"][0]["cardio_target"] = {}
        self.assertEqual(
            validate_and_normalize_plan(explicit_free)["sessions"][0][
                "cardio_target"
            ],
            {},
        )

        hidden_strength = cardio_plan()
        hidden_strength["sessions"][0]["exercises"] = [
            {
                "name": "Hidden exercise",
                "sets": [{"weight_kg": 20, "reps": 10}],
            }
        ]
        self.assertInvalid(hidden_strength)

    def test_exercise_note_presence_is_lossless_and_null_is_rejected(self) -> None:
        omitted = strength_plan()
        del omitted["sessions"][0]["exercises"][0]["note"]
        normalized_omitted = validate_and_normalize_plan(omitted)
        self.assertNotIn(
            "note",
            normalized_omitted["sessions"][0]["exercises"][0],
        )

        explicit_empty = strength_plan()
        explicit_empty["sessions"][0]["exercises"][0]["note"] = ""
        normalized_empty = validate_and_normalize_plan(explicit_empty)
        self.assertEqual(
            normalized_empty["sessions"][0]["exercises"][0]["note"],
            "",
        )

        invalid = strength_plan()
        invalid["sessions"][0]["exercises"][0]["note"] = None
        self.assertInvalid(invalid)

    def test_cardio_target_keeps_only_finite_bounded_values(self) -> None:
        normalized = validate_and_normalize_plan(cardio_plan())
        self.assertEqual(
            normalized["sessions"][0]["cardio_target"],
            {"duration_min": 30, "distance_km": 5.0},
        )

    def test_exact_upper_and_numeric_boundaries_are_accepted(self) -> None:
        plan = strength_plan()
        plan["name"] = "n" * 128
        plan["rationale"] = "r" * 2_048
        declare_update(plan, replacement_name="p" * 128)
        session = plan["sessions"][0]
        session["name"] = "s" * 128
        exercise = session["exercises"][0]
        exercise["name"] = "e" * 128
        exercise["note"] = "x" * 512
        exercise["sets"] = [
            {"weight_kg": 0, "reps": 1},
            {"weight_kg": 1_000, "reps": 100},
        ]

        normalized = validate_and_normalize_plan(plan)

        self.assertEqual(len(normalized["name"].encode("utf-8")), 128)
        self.assertEqual(
            normalized["sessions"][0]["exercises"][0]["sets"][-1],
            {"weight_kg": 1_000.0, "reps": 100},
        )

        cardio = cardio_plan()
        cardio["sessions"][0]["cardio_target"] = {
            "duration_min": 1_440,
            "distance_km": 1_000,
        }
        self.assertEqual(
            validate_and_normalize_plan(cardio)["sessions"][0]["cardio_target"],
            {"duration_min": 1_440, "distance_km": 1_000.0},
        )

    def test_utf8_text_bounds_match_swift_byte_limits(self) -> None:
        mutations = []

        blank_plan = strength_plan()
        blank_plan["name"] = " \n\t"
        mutations.append(blank_plan)

        invalid_utf8 = strength_plan()
        invalid_utf8["name"] = "\ud800"
        mutations.append(invalid_utf8)

        plan_name_bytes = strength_plan()
        plan_name_bytes["name"] = "é" * 65
        mutations.append(plan_name_bytes)

        rationale_bytes = strength_plan()
        rationale_bytes["rationale"] = "é" * 1_025
        mutations.append(rationale_bytes)

        blank_replacement = strength_plan()
        blank_replacement["replaces_plan"] = "  "
        mutations.append(blank_replacement)

        replacement_bytes = strength_plan()
        replacement_bytes["replaces_plan"] = "é" * 65
        mutations.append(replacement_bytes)

        session_name_bytes = strength_plan()
        session_name_bytes["sessions"][0]["name"] = "é" * 65
        mutations.append(session_name_bytes)

        exercise_name_bytes = strength_plan()
        exercise_name_bytes["sessions"][0]["exercises"][0]["name"] = "é" * 65
        mutations.append(exercise_name_bytes)

        note_bytes = strength_plan()
        note_bytes["sessions"][0]["exercises"][0]["note"] = "é" * 257
        mutations.append(note_bytes)

        for plan in mutations:
            with self.subTest(name=plan.get("name")):
                self.assertInvalid(plan)

    def test_required_text_uses_foundation_whitespace_scalar_set(self) -> None:
        foundation_whitespace = (
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
        for code_point in foundation_whitespace:
            plan = strength_plan()
            plan["name"] = chr(code_point)
            with self.subTest(code_point=f"U+{code_point:04X}"):
                self.assertInvalid(plan)

        for field_path in (
            ("name",),
            ("replaces_plan",),
            ("sessions", 0, "name"),
            ("sessions", 0, "exercises", 0, "name"),
        ):
            plan = strength_plan()
            target = plan
            for component in field_path[:-1]:
                target = target[component]
            target[field_path[-1]] = "\u200b"
            with self.subTest(field_path=field_path):
                self.assertInvalid(plan)

        for foundation_nonwhitespace in ("\ufeff", "\u001c"):
            plan = strength_plan()
            plan["name"] = foundation_nonwhitespace
            normalized = validate_and_normalize_plan(plan)
            self.assertEqual(normalized["name"], foundation_nonwhitespace)

    def test_collection_counts_match_swift_limits(self) -> None:
        empty_sessions = strength_plan()
        empty_sessions["sessions"] = []

        too_many_sessions = strength_plan()
        too_many_sessions["sessions"] = [
            copy.deepcopy(strength_plan()["sessions"][0]) for _ in range(15)
        ]
        for index, session in enumerate(too_many_sessions["sessions"]):
            session["session_id"] = str(uuid.uuid4())

        empty_strength = strength_plan()
        empty_strength["sessions"][0]["exercises"] = []

        too_many_exercises = strength_plan()
        exercise = strength_plan()["sessions"][0]["exercises"][0]
        too_many_exercises["sessions"][0]["exercises"] = [
            copy.deepcopy(exercise) for _ in range(33)
        ]

        empty_sets = strength_plan()
        empty_sets["sessions"][0]["exercises"][0]["sets"] = []

        too_many_sets = strength_plan()
        too_many_sets["sessions"][0]["exercises"][0]["sets"] = [
            {"weight_kg": 1, "reps": 1} for _ in range(21)
        ]

        for plan in (
            empty_sessions,
            too_many_sessions,
            empty_strength,
            too_many_exercises,
            empty_sets,
            too_many_sets,
        ):
            with self.subTest(plan=plan):
                self.assertInvalid(plan)

    def test_four_maximum_plans_fit_swift_response_byte_limit(self) -> None:
        plan = {
            "schema_version": "1.0",
            "name": "n" * 128,
            "rationale": "r" * 2_048,
            "replaces_plan": "p" * 128,
            "replaces_plan_id": str(uuid.uuid4()),
            "base_plan_fingerprint": PLAN_FINGERPRINT,
            "removed_session_ids": [],
            "removed_exercise_ids": [],
            "sessions": [],
        }
        for _ in range(14):
            plan["sessions"].append(
                {
                    "session_id": str(uuid.uuid4()),
                    "name": "s" * 128,
                    "kind": "strength",
                    "exercises": [
                        {
                            "exercise_id": str(uuid.uuid4()),
                            "name": "e" * 128,
                            "note": "x" * 512,
                            "rest_seconds": MAXIMUM_REST_SECONDS,
                            "warmup_sets": MAXIMUM_WARMUP_SETS,
                            "warmup_weight_kg": MAXIMUM_WARMUP_WEIGHT_KG,
                            "bilateral": True,
                            "sets": [
                                {
                                    "weight_kg": 1_000,
                                    "reps": 99,
                                    "reps_max": 100,
                                }
                                for _ in range(20)
                            ],
                        }
                        for _ in range(32)
                    ],
                }
            )
        normalized = validate_and_normalize_plan(plan)
        plans = []
        for _ in range(4):
            output = copy.deepcopy(normalized)
            output["plan_id"] = str(uuid.uuid4())
            output["origin"] = {
                "version": 1,
                "conversation_id": str(uuid.uuid4()),
            }
            plans.append(output)

        encoded = json.dumps(
            {"plans": plans},
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")

        self.assertLessEqual(len(encoded), MAXIMUM_PENDING_RESPONSE_BYTES)

    def test_set_numbers_must_be_finite_exact_and_in_range(self) -> None:
        invalid_weights = [math.nan, math.inf, -0.01, 1_000.01, True, "60"]
        invalid_repetitions = [False, 0, 101, 1.5, math.inf, "8"]

        for value in invalid_weights:
            plan = strength_plan()
            plan["sessions"][0]["exercises"][0]["sets"][0]["weight_kg"] = value
            with self.subTest(weight=value):
                self.assertInvalid(plan)
        for value in invalid_repetitions:
            plan = strength_plan()
            plan["sessions"][0]["exercises"][0]["sets"][0]["reps"] = value
            with self.subTest(reps=value):
                self.assertInvalid(plan)

    def test_session_ids_must_be_valid_and_unique_after_normalization(self) -> None:
        invalid = strength_plan()
        invalid["sessions"][0]["session_id"] = "not-a-uuid"
        self.assertInvalid(invalid)

        duplicate = strength_plan()
        second = copy.deepcopy(duplicate["sessions"][0])
        second["name"] = "Pull"
        second["session_id"] = SESSION_ID.upper()
        duplicate["sessions"].append(second)
        self.assertInvalid(duplicate)

    def test_exercise_ids_must_be_valid_and_unique_across_sessions(self) -> None:
        invalid = strength_plan()
        invalid["sessions"][0]["exercises"][0]["exercise_id"] = "not-a-uuid"
        self.assertInvalid(invalid)

        duplicate = strength_plan()
        duplicate["sessions"][0]["exercises"][0]["exercise_id"] = EXERCISE_ID
        second = copy.deepcopy(duplicate["sessions"][0])
        second["session_id"] = str(uuid.uuid4())
        second["name"] = "Pull"
        second["exercises"][0]["exercise_id"] = EXERCISE_ID.upper()
        duplicate["sessions"].append(second)
        self.assertInvalid(duplicate)

    def test_cardio_rules_match_swift_parser(self) -> None:
        invalid_kind = cardio_plan()
        invalid_kind["sessions"][0]["kind"] = "mobility"

        strength_target = strength_plan()
        strength_target["sessions"][0]["cardio_target"] = {"duration_min": 30}

        malformed_target = cardio_plan()
        malformed_target["sessions"][0]["cardio_target"] = []

        for plan in (invalid_kind, strength_target, malformed_target):
            self.assertInvalid(plan)

        for duration in (9, 1_441, 10.5, True, math.inf):
            plan = cardio_plan()
            plan["sessions"][0]["cardio_target"] = {"duration_min": duration}
            with self.subTest(duration=duration):
                self.assertInvalid(plan)

        for distance in (0, -1, 1_000.01, True, math.nan, math.inf):
            plan = cardio_plan()
            plan["sessions"][0]["cardio_target"] = {"distance_km": distance}
            with self.subTest(distance=distance):
                self.assertInvalid(plan)


class StorePlanContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = Path(self.temp_dir.name) / "kore.db"
        self.store = KoreStore(
            self.db_path,
            instance_id="main-test",
            principal="private-main",
        )
        self.store.init()

    @staticmethod
    def origin(nonce: str | None = None) -> VerifiedOrigin:
        return VerifiedOrigin(
            version=1,
            conversation_id=CONVERSATION_ID,
            principal="private-main",
            nonce=nonce or secrets.token_urlsafe(24),
            issued_at=1_800_000_000,
            expires_at=1_800_000_180,
        )

    def test_invalid_plans_never_consume_nonce_or_enter_pending(self) -> None:
        overlimit = strength_plan()
        overlimit["name"] = "é" * 65
        nonfinite = strength_plan()
        nonfinite["sessions"][0]["exercises"][0]["sets"][0][
            "weight_kg"
        ] = math.nan
        blank = strength_plan()
        blank["sessions"][0]["name"] = "  "
        duplicate = strength_plan()
        second = copy.deepcopy(duplicate["sessions"][0])
        second["name"] = "Pull"
        second["session_id"] = SESSION_ID.upper()
        duplicate["sessions"].append(second)
        invalid_cardio = cardio_plan()
        invalid_cardio["sessions"][0]["cardio_target"] = {"duration_min": 9}

        retry_origin = self.origin(nonce="abcdefghijklmnopqrstuvwxyz123456")
        cases = [
            (overlimit, retry_origin),
            (nonfinite, self.origin()),
            (blank, self.origin()),
            (duplicate, self.origin()),
            (invalid_cardio, self.origin()),
        ]
        for invalid, origin in cases:
            with self.subTest(plan=invalid), self.assertRaises(InvalidPlanProposal):
                self.store.add_plan(invalid, origin)

        self.assertEqual(self.store.pending_plans("private-main"), [])
        self.store.add_plan(strength_plan(), retry_origin)
        self.assertEqual(len(self.store.pending_plans("private-main")), 1)

    def test_corrupt_origin_bound_row_is_hidden_instead_of_poisoning_batch(self) -> None:
        invalid = strength_plan()
        invalid["name"] = " "
        invalid["plan_id"] = str(uuid.uuid4())
        invalid["origin"] = {
            "version": 1,
            "conversation_id": CONVERSATION_ID,
        }
        with sqlite3.connect(self.db_path) as db:
            db.execute(
                "INSERT INTO plan("
                "id, data, status, created_at, acked_at, "
                "origin_principal, conversation_id) "
                "VALUES (?, ?, 'pending', ?, NULL, ?, ?)",
                (
                    invalid["plan_id"],
                    json.dumps(invalid),
                    1.0,
                    "private-main",
                    CONVERSATION_ID,
                ),
            )

        self.assertEqual(self.store.pending_plans("private-main"), [])

    def test_pending_read_never_exceeds_swift_reply_limit(self) -> None:
        with sqlite3.connect(self.db_path) as db:
            for index in range(5):
                plan = strength_plan()
                plan_id = str(uuid.uuid4())
                plan["plan_id"] = plan_id
                plan["origin"] = {
                    "version": 1,
                    "conversation_id": CONVERSATION_ID,
                }
                db.execute(
                    "INSERT INTO plan("
                    "id, data, status, created_at, acked_at, "
                    "origin_principal, conversation_id) "
                    "VALUES (?, ?, 'pending', ?, NULL, ?, ?)",
                    (
                        plan_id,
                        json.dumps(plan),
                        float(index),
                        "private-main",
                        CONVERSATION_ID,
                    ),
                )

        self.assertEqual(len(self.store.pending_plans("private-main")), 4)

    def test_stored_cardio_json_never_contains_null_target(self) -> None:
        plan = cardio_plan()
        plan["sessions"][0]["cardio_target"] = None
        plan_id = self.store.add_plan(plan, self.origin())

        with sqlite3.connect(self.db_path) as db:
            raw = db.execute(
                "SELECT data FROM plan WHERE id = ?", (plan_id,)
            ).fetchone()[0]
        stored = json.loads(raw)
        self.assertNotIn("cardio_target", stored["sessions"][0])
        self.assertNotIn(
            "cardio_target", self.store.pending_plans("private-main")[0]["sessions"][0]
        )


if __name__ == "__main__":
    unittest.main()
