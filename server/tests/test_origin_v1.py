from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import sqlite3
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


SERVER_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_ROOT))

from origin_ticket import (  # noqa: E402
    InvalidOriginTicket,
    OriginTicketPolicy,
    issue_origin_ticket,
    verify_origin_ticket,
)
from store import (  # noqa: E402
    InstanceOwnershipError,
    KoreStore,
    OriginReplayError,
    PendingPlanLimitError,
    StoreConfigurationError,
    _store_from_environment,
)


CONVERSATION_ID = "3f84835e-35f4-4f99-bfc8-3ee3c7b2f64f"


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _sign_raw_claims(claims: dict[str, object], secret: bytes) -> str:
    payload = json.dumps(
        claims,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    encoded = _b64url(payload)
    signature = hmac.new(secret, encoded.encode("ascii"), hashlib.sha256).digest()
    return f"{encoded}.{_b64url(signature)}"


class OriginTicketTests(unittest.TestCase):
    def setUp(self) -> None:
        self.secret = secrets.token_bytes(32)
        self.policy = OriginTicketPolicy(
            principal="private-main",
            max_ttl_seconds=90,
            clock_skew_seconds=5,
        )
        self.now = 1_800_000_000

    def ticket(
        self,
        *,
        principal: str = "private-main",
        issued_at: int | None = None,
        expires_at: int | None = None,
        nonce: str | None = None,
    ) -> str:
        issued_at = self.now if issued_at is None else issued_at
        expires_at = self.now + 60 if expires_at is None else expires_at
        return issue_origin_ticket(
            secret=self.secret,
            conversation_id=CONVERSATION_ID,
            principal=principal,
            nonce=nonce or secrets.token_urlsafe(24),
            issued_at=issued_at,
            expires_at=expires_at,
        )

    def test_valid_ticket_round_trips_exact_claims(self) -> None:
        ticket = self.ticket(nonce="abcdefghijklmnopqrstuvwxyz123456")

        origin = verify_origin_ticket(
            ticket,
            secret=self.secret,
            policy=self.policy,
            now=self.now,
        )

        self.assertEqual(origin.version, 1)
        self.assertEqual(origin.conversation_id, CONVERSATION_ID)
        self.assertEqual(origin.principal, "private-main")
        self.assertEqual(origin.nonce, "abcdefghijklmnopqrstuvwxyz123456")
        self.assertEqual(origin.issued_at, self.now)
        self.assertEqual(origin.expires_at, self.now + 60)

    def test_forged_signature_is_rejected(self) -> None:
        ticket = issue_origin_ticket(
            secret=secrets.token_bytes(32),
            conversation_id=CONVERSATION_ID,
            principal="private-main",
            nonce=secrets.token_urlsafe(24),
            issued_at=self.now,
            expires_at=self.now + 60,
        )

        with self.assertRaises(InvalidOriginTicket):
            verify_origin_ticket(
                ticket,
                secret=self.secret,
                policy=self.policy,
                now=self.now,
            )

    def test_expired_ticket_is_rejected(self) -> None:
        with self.assertRaisesRegex(InvalidOriginTicket, "expired"):
            verify_origin_ticket(
                self.ticket(issued_at=self.now - 90, expires_at=self.now - 6),
                secret=self.secret,
                policy=self.policy,
                now=self.now,
            )

    def test_future_ticket_beyond_clock_skew_is_rejected(self) -> None:
        with self.assertRaisesRegex(InvalidOriginTicket, "future"):
            verify_origin_ticket(
                self.ticket(issued_at=self.now + 6, expires_at=self.now + 66),
                secret=self.secret,
                policy=self.policy,
                now=self.now,
            )

    def test_overlong_ttl_is_rejected(self) -> None:
        with self.assertRaisesRegex(InvalidOriginTicket, "TTL"):
            verify_origin_ticket(
                self.ticket(expires_at=self.now + 91),
                secret=self.secret,
                policy=self.policy,
                now=self.now,
            )

    def test_wrong_principal_is_rejected(self) -> None:
        with self.assertRaisesRegex(InvalidOriginTicket, "principal"):
            verify_origin_ticket(
                self.ticket(principal="private-other"),
                secret=self.secret,
                policy=self.policy,
                now=self.now,
            )

    def test_policy_has_hard_ttl_and_clock_skew_caps(self) -> None:
        with self.assertRaises(ValueError):
            OriginTicketPolicy(principal="private-main", max_ttl_seconds=301)
        with self.assertRaises(ValueError):
            OriginTicketPolicy(principal="private-main", clock_skew_seconds=61)

    def test_claims_must_have_exact_fields_and_types(self) -> None:
        base: dict[str, object] = {
            "version": 1,
            "conversation_id": CONVERSATION_ID,
            "principal": "private-main",
            "nonce": secrets.token_urlsafe(24),
            "issued_at": self.now,
            "expires_at": self.now + 60,
        }
        variants = []
        with_extra = dict(base)
        with_extra["role"] = "admin"
        variants.append(with_extra)
        with_boolean_time = dict(base)
        with_boolean_time["issued_at"] = True
        variants.append(with_boolean_time)
        with_uppercase_uuid = dict(base)
        with_uppercase_uuid["conversation_id"] = CONVERSATION_ID.upper()
        variants.append(with_uppercase_uuid)
        missing_nonce = dict(base)
        del missing_nonce["nonce"]
        variants.append(missing_nonce)

        for claims in variants:
            with self.subTest(claims=claims), self.assertRaises(InvalidOriginTicket):
                verify_origin_ticket(
                    _sign_raw_claims(claims, self.secret),
                    secret=self.secret,
                    policy=self.policy,
                    now=self.now,
                )


class StoreOriginTests(unittest.TestCase):
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
    def origin(
        *,
        principal: str = "private-main",
        nonce: str | None = None,
        conversation_id: str = CONVERSATION_ID,
    ):
        from origin_ticket import VerifiedOrigin

        return VerifiedOrigin(
            version=1,
            conversation_id=conversation_id,
            principal=principal,
            nonce=nonce or secrets.token_urlsafe(24),
            issued_at=1_800_000_000,
            expires_at=1_800_000_060,
        )

    @staticmethod
    def plan(name: str = "Strength A") -> dict[str, object]:
        return {
            "schema_version": "1.0",
            "name": name,
            "rationale": "Recovery is stable.",
            "sessions": [
                {
                    "name": "Strength",
                    "exercises": [
                        {
                            "name": "Squat",
                            "note": "",
                            "sets": [{"weight_kg": 40, "reps": 8}],
                        }
                    ],
                }
            ],
        }

    def test_migration_adds_origin_columns_and_nonce_table(self) -> None:
        with sqlite3.connect(self.db_path) as db:
            plan_columns = {
                row[1] for row in db.execute("PRAGMA table_info(plan)").fetchall()
            }
            nonce_columns = {
                row[1]
                for row in db.execute(
                    "PRAGMA table_info(consumed_origin_nonce)"
                ).fetchall()
            }

        self.assertTrue({"origin_principal", "conversation_id"} <= plan_columns)
        self.assertEqual(
            nonce_columns,
            {"nonce", "principal", "consumed_at", "expires_at"},
        )

    def test_snapshot_storage_keeps_live_single_row_contract(self) -> None:
        self.store.save_snapshot({"recovery": {"readiness": 81}})

        snapshot = self.store.get_snapshot()

        self.assertEqual(snapshot["recovery"], {"readiness": 81})
        self.assertIsInstance(snapshot["_updated_at"], float)
        with sqlite3.connect(self.db_path) as db:
            rows = db.execute(
                "SELECT id, instance_id, principal FROM snapshot"
            ).fetchall()
            owner = db.execute(
                "SELECT instance_id, principal FROM kore_instance_owner"
            ).fetchone()
        self.assertEqual(rows, [(1, "main-test", "private-main")])
        self.assertEqual(owner, ("main-test", "private-main"))

    def test_migration_preserves_existing_plan_rows(self) -> None:
        legacy_db = Path(self.temp_dir.name) / "legacy.db"
        with sqlite3.connect(legacy_db) as db:
            db.execute(
                "CREATE TABLE snapshot ("
                "id INTEGER PRIMARY KEY CHECK(id = 1), "
                "data TEXT NOT NULL, updated_at REAL NOT NULL)"
            )
            db.execute(
                "CREATE TABLE plan ("
                "id TEXT PRIMARY KEY, data TEXT NOT NULL, status TEXT NOT NULL, "
                "created_at REAL NOT NULL, acked_at REAL)"
            )
            db.execute(
                "INSERT INTO snapshot(id, data, updated_at) VALUES (1, ?, ?)",
                (json.dumps({"legacy": "snapshot"}), 3.0),
            )
            db.execute(
                "INSERT INTO plan VALUES (?, ?, 'accepted', ?, ?)",
                ("existing", json.dumps({"plan_id": "existing"}), 1.0, 2.0),
            )

        for confirmation in (None, "other-test:private-main"):
            unconfirmed = KoreStore(
                legacy_db,
                instance_id="main-test",
                principal="private-main",
                legacy_owner_confirmation=confirmation,
            )
            with self.subTest(confirmation=confirmation), self.assertRaisesRegex(
                InstanceOwnershipError,
                "no owner",
            ):
                unconfirmed.init()

        with sqlite3.connect(legacy_db) as db:
            self.assertIsNone(
                db.execute(
                    "SELECT 1 FROM sqlite_master "
                    "WHERE type = 'table' AND name = 'kore_instance_owner'"
                ).fetchone()
            )

        confirmed = KoreStore(
            legacy_db,
            instance_id="main-test",
            principal="private-main",
            legacy_owner_confirmation="main-test:private-main",
        )
        confirmed.init()

        with sqlite3.connect(legacy_db) as db:
            row = db.execute(
                "SELECT id, data, status, created_at, acked_at, "
                "origin_principal, conversation_id FROM plan"
            ).fetchone()
            owner = db.execute(
                "SELECT instance_id, principal FROM kore_instance_owner"
            ).fetchone()
            snapshot_columns = {
                column[1]
                for column in db.execute("PRAGMA table_info(snapshot)").fetchall()
            }
            snapshot = db.execute(
                "SELECT instance_id, principal, data, updated_at FROM snapshot"
            ).fetchone()
        self.assertEqual(
            row,
            (
                "existing",
                json.dumps({"plan_id": "existing"}),
                "accepted",
                1.0,
                2.0,
                None,
                None,
            ),
        )
        self.assertEqual(owner, ("main-test", "private-main"))
        self.assertTrue({"instance_id", "principal"} <= snapshot_columns)
        self.assertEqual(
            snapshot,
            (
                "main-test",
                "private-main",
                json.dumps({"legacy": "snapshot"}),
                3.0,
            ),
        )

    def test_database_owner_mismatch_never_reads_or_rebinds_snapshot(self) -> None:
        self.store.save_snapshot({"owner": "main"})
        foreign = KoreStore(
            self.db_path,
            instance_id="second-user-test",
            principal="private-second-user",
        )

        with self.assertRaisesRegex(InstanceOwnershipError, "owner"):
            foreign.init()
        with self.assertRaisesRegex(InstanceOwnershipError, "owner"):
            foreign.get_snapshot()

        self.assertEqual(self.store.get_snapshot()["owner"], "main")

    def test_legacy_foreign_principal_cannot_be_claimed_by_confirmation(self) -> None:
        legacy_db = Path(self.temp_dir.name) / "foreign.db"
        with sqlite3.connect(legacy_db) as db:
            db.execute(
                "CREATE TABLE plan ("
                "id TEXT PRIMARY KEY, data TEXT NOT NULL, status TEXT NOT NULL, "
                "created_at REAL NOT NULL, acked_at REAL, "
                "origin_principal TEXT, conversation_id TEXT)"
            )
            db.execute(
                "INSERT INTO plan VALUES (?, ?, 'pending', ?, NULL, ?, ?)",
                (
                    "foreign",
                    json.dumps({"plan_id": "foreign"}),
                    1.0,
                    "private-other",
                    CONVERSATION_ID,
                ),
            )

        store = KoreStore(
            legacy_db,
            instance_id="main-test",
            principal="private-main",
            legacy_owner_confirmation="main-test:private-main",
        )
        with self.assertRaisesRegex(InstanceOwnershipError, "another principal"):
            store.init()

    def test_default_store_configuration_has_no_implicit_instance_or_db(self) -> None:
        valid = {
            "KORE_DB_PATH": str(Path(self.temp_dir.name) / "configured.db"),
            "KORE_INSTANCE_ID": "configured-test",
            "KORE_PLAN_ORIGIN_PRINCIPAL": "private-configured",
        }
        configured = _store_from_environment(valid)
        self.assertEqual(configured.instance_id, "configured-test")
        self.assertEqual(configured.principal, "private-configured")

        for missing in (
            "KORE_DB_PATH",
            "KORE_INSTANCE_ID",
            "KORE_PLAN_ORIGIN_PRINCIPAL",
        ):
            environment = dict(valid)
            del environment[missing]
            with self.subTest(missing=missing), self.assertRaisesRegex(
                StoreConfigurationError,
                missing,
            ):
                _store_from_environment(environment)

        relative = dict(valid)
        relative["KORE_DB_PATH"] = "kore.db"
        with self.assertRaisesRegex(StoreConfigurationError, "absolute"):
            _store_from_environment(relative)

    def test_replay_is_rejected(self) -> None:
        origin = self.origin(nonce="abcdefghijklmnopqrstuvwxyz123456")
        self.store.add_plan(self.plan(), origin)

        with self.assertRaises(OriginReplayError):
            self.store.add_plan(self.plan("Strength B"), origin)

    def test_concurrent_replay_creates_exactly_one_plan(self) -> None:
        origin = self.origin(nonce="abcdefghijklmnopqrstuvwxyz123456")
        barrier = threading.Barrier(2)

        def add() -> str:
            barrier.wait()
            return self.store.add_plan(self.plan(), origin)

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = []
            for future in [executor.submit(add), executor.submit(add)]:
                try:
                    outcomes.append(("ok", future.result()))
                except OriginReplayError:
                    outcomes.append(("replay", None))

        self.assertEqual([kind for kind, _ in outcomes].count("ok"), 1)
        self.assertEqual([kind for kind, _ in outcomes].count("replay"), 1)
        self.assertEqual(len(self.store.pending_plans("private-main")), 1)

    def test_legacy_pending_plan_without_origin_is_hidden(self) -> None:
        with sqlite3.connect(self.db_path) as db:
            db.execute(
                "INSERT INTO plan(id, data, status, created_at, acked_at) "
                "VALUES (?, ?, 'pending', ?, NULL)",
                ("legacy", json.dumps({"plan_id": "legacy", **self.plan()}), 1.0),
            )

        self.assertEqual(self.store.pending_plans("private-main"), [])

    def test_foreign_plan_principal_is_rejected_by_store_owner(self) -> None:
        own_id = self.store.add_plan(self.plan("Own"), self.origin())

        with self.assertRaisesRegex(InstanceOwnershipError, "principal"):
            self.store.add_plan(
                self.plan("Other"), self.origin(principal="private-other")
            )
        with self.assertRaisesRegex(InstanceOwnershipError, "principal"):
            self.store.pending_plans("private-other")
        self.assertEqual(
            [plan["plan_id"] for plan in self.store.pending_plans("private-main")],
            [own_id],
        )

    def test_pending_cap_is_four_and_rejected_nonce_is_not_consumed(self) -> None:
        plan_ids = [
            self.store.add_plan(self.plan(f"Plan {index}"), self.origin())
            for index in range(4)
        ]
        fifth_origin = self.origin(nonce="abcdefghijklmnopqrstuvwxyz123456")

        with self.assertRaises(PendingPlanLimitError):
            self.store.add_plan(self.plan("Plan 5"), fifth_origin)

        self.assertEqual(len(self.store.pending_plans("private-main")), 4)
        self.assertTrue(
            self.store.ack_plan(
                plan_ids[0], accepted=False, principal="private-main"
            )
        )
        self.store.add_plan(self.plan("Plan 5"), fifth_origin)
        self.assertEqual(len(self.store.pending_plans("private-main")), 4)

    def test_concurrent_proposals_cannot_exceed_pending_cap(self) -> None:
        for index in range(3):
            self.store.add_plan(self.plan(f"Plan {index}"), self.origin())
        barrier = threading.Barrier(2)

        def add(origin) -> str:
            barrier.wait()
            return self.store.add_plan(self.plan("Concurrent"), origin)

        origins = [self.origin(), self.origin()]
        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = []
            for future in [executor.submit(add, origin) for origin in origins]:
                try:
                    outcomes.append(("ok", future.result()))
                except PendingPlanLimitError:
                    outcomes.append(("limit", None))

        self.assertEqual([kind for kind, _ in outcomes].count("ok"), 1)
        self.assertEqual([kind for kind, _ in outcomes].count("limit"), 1)
        self.assertEqual(len(self.store.pending_plans("private-main")), 4)

    def test_ack_rejects_a_principal_outside_the_database_owner(self) -> None:
        plan_id = self.store.add_plan(self.plan(), self.origin())

        with self.assertRaisesRegex(InstanceOwnershipError, "principal"):
            self.store.ack_plan(plan_id, accepted=True, principal="private-other")

        self.assertTrue(
            self.store.ack_plan(plan_id, accepted=False, principal="private-main")
        )
        self.assertEqual(self.store.pending_plans("private-main"), [])

    def test_ack_retry_is_idempotent_but_opposite_decision_conflicts(self) -> None:
        plan_id = self.store.add_plan(self.plan(), self.origin())
        self.assertTrue(
            self.store.ack_plan(plan_id, accepted=True, principal="private-main")
        )
        with sqlite3.connect(self.db_path) as db:
            first_acked_at = db.execute(
                "SELECT acked_at FROM plan WHERE id = ?", (plan_id,)
            ).fetchone()[0]

        self.assertTrue(
            self.store.ack_plan(plan_id, accepted=True, principal="private-main")
        )
        self.assertFalse(
            self.store.ack_plan(plan_id, accepted=False, principal="private-main")
        )
        with sqlite3.connect(self.db_path) as db:
            status, retried_acked_at = db.execute(
                "SELECT status, acked_at FROM plan WHERE id = ?", (plan_id,)
            ).fetchone()
        self.assertEqual(status, "accepted")
        self.assertEqual(retried_acked_at, first_acked_at)

    def test_pending_response_plan_has_exact_inline_origin_shape(self) -> None:
        plan_id = self.store.add_plan(self.plan(), self.origin())

        response = {"plans": self.store.pending_plans("private-main")}

        self.assertEqual(
            response,
            {
                "plans": [
                    {
                        "schema_version": "1.0",
                        "name": "Strength A",
                        "rationale": "Recovery is stable.",
                        "sessions": [
                            {
                                "name": "Strength",
                                "exercises": [
                                    {
                                        "name": "Squat",
                                        "note": "",
                                        "sets": [
                                            {"weight_kg": 40.0, "reps": 8}
                                        ],
                                    }
                                ],
                            }
                        ],
                        "plan_id": plan_id,
                        "origin": {
                            "version": 1,
                            "conversation_id": CONVERSATION_ID,
                        },
                    }
                ]
            },
        )

    def test_plan_write_and_nonce_consumption_are_atomic(self) -> None:
        origin = self.origin(nonce="abcdefghijklmnopqrstuvwxyz123456")
        with sqlite3.connect(self.db_path) as db:
            db.execute(
                "CREATE TRIGGER block_plan_insert BEFORE INSERT ON plan "
                "BEGIN SELECT RAISE(ABORT, 'blocked'); END"
            )

        with self.assertRaises(sqlite3.IntegrityError):
            self.store.add_plan(self.plan(), origin)

        with sqlite3.connect(self.db_path) as db:
            db.execute("DROP TRIGGER block_plan_insert")

        plan_id = self.store.add_plan(self.plan(), origin)
        self.assertEqual(
            [plan["plan_id"] for plan in self.store.pending_plans("private-main")],
            [plan_id],
        )


if __name__ == "__main__":
    unittest.main()
