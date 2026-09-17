from __future__ import annotations

import asyncio
import importlib
import logging
import os
import secrets
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path

import fastmcp
from fastmcp import Client
from mcp.shared.message import SessionMessage
from mcp.types import JSONRPCMessage, JSONRPCRequest
from rich.logging import RichHandler


SERVER_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_ROOT))


class _RecordCollector(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


class FastMCPIntegrationTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.secret_text = secrets.token_urlsafe(48)
        os.environ.update(
            {
                "KORE_DB_PATH": str(Path(cls.temp_dir.name) / "mcp.db"),
                "KORE_INSTANCE_ID": "mcp-integration",
                "KORE_MCP_TOKEN": secrets.token_urlsafe(32),
                "KORE_MCP_CLIENT_ID": "integration",
                "KORE_PLAN_ORIGIN_PRINCIPAL": "private-mcp",
                "KORE_PLAN_ORIGIN_SECRET": cls.secret_text,
                "KORE_PLAN_ORIGIN_MAX_TTL_SECONDS": "300",
                "KORE_PLAN_ORIGIN_CLOCK_SKEW_SECONDS": "5",
            }
        )
        os.environ.pop("FASTMCP_LOG_LEVEL", None)
        # Simulate the dangerous import order: FastMCP already configured a
        # permissive Rich logger, then logging was disabled without removing it.
        fastmcp.settings.log_enabled = False
        fastmcp.settings.log_level = "DEBUG"
        fastmcp.settings.enable_rich_logging = True
        fastmcp.settings.enable_rich_tracebacks = True
        legacy_root_rich_handler = RichHandler(rich_tracebacks=True)
        legacy_root_rich_handler.setLevel(logging.DEBUG)
        logging.getLogger().addHandler(legacy_root_rich_handler)
        for namespace in ("", "fastmcp", "mcp"):
            logger = logging.getLogger(namespace)
            logger.setLevel(logging.DEBUG)
            for handler in logger.handlers:
                handler.setLevel(logging.DEBUG)
        for name in ("mcp_server", "store", "models", "origin_ticket"):
            sys.modules.pop(name, None)
        cls.mcp_server = importlib.import_module("mcp_server")
        cls.store = importlib.import_module("store")
        cls.origin_ticket = importlib.import_module("origin_ticket")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_dir.cleanup()

    @classmethod
    def ticket(cls, conversation_id: str | None = None) -> tuple[str, str]:
        now = int(time.time())
        conversation_id = conversation_id or str(uuid.uuid4())
        return (
            cls.origin_ticket.issue_origin_ticket(
                secret=cls.secret_text.encode("utf-8"),
                conversation_id=conversation_id,
                principal="private-mcp",
                nonce=secrets.token_urlsafe(24),
                issued_at=now,
                expires_at=now + 180,
            ),
            conversation_id,
        )

    async def test_in_memory_tool_schema_and_origin_bound_plan_round_trip(self) -> None:
        ticket, conversation_id = self.ticket()
        exercise_id = str(uuid.uuid4())
        replacement_plan_id = str(uuid.uuid4())
        plan = {
            "schema_version": "1.0",
            "name": "Hybrid",
            "replaces_plan_id": replacement_plan_id.upper(),
            "base_plan_fingerprint": "sha256:" + "a" * 64,
            "removed_session_ids": [],
            "removed_exercise_ids": [],
            "sessions": [
                {
                    "session_id": str(uuid.uuid4()).upper(),
                    "name": "Run",
                    "kind": "cardio",
                    "cardio_target": {},
                    "exercises": [],
                },
                {
                    "session_id": str(uuid.uuid4()),
                    "name": "Strength",
                    "kind": "strength",
                    "exercises": [
                        {
                            "exercise_id": exercise_id.upper(),
                            "name": "Bench Press",
                            "note": "Barbell",
                            "rest_seconds": 180,
                            "warmup_sets": 2,
                            "warmup_weight_kg": 40,
                            "bilateral": False,
                            "sets": [
                                {"weight_kg": 80, "reps": 8, "reps_max": 12}
                            ],
                        },
                        {
                            "name": "Row",
                            "sets": [{"weight_kg": 60, "reps": 10}],
                        },
                        {
                            "name": "Fly",
                            "note": "",
                            "sets": [{"weight_kg": 15, "reps": 12}],
                        },
                    ],
                },
            ],
        }

        async with Client(self.mcp_server.mcp) as client:
            tools = await client.list_tools()
            proposal_tool = next(
                tool for tool in tools if tool.name == "propose_training_plan"
            )
            plan_read_tool = next(
                tool for tool in tools if tool.name == "get_training_plans"
            )
            self.assertIn("replaces_plan_id", plan_read_tool.description)
            self.assertIn("read-only", plan_read_tool.description)
            self.assertIn("niemals", plan_read_tool.description)
            self.assertIn("origin_ticket", proposal_tool.inputSchema["required"])
            plan_schema = proposal_tool.inputSchema["properties"]["plan"]
            self.assertIn("replaces_plan_id", plan_schema["properties"])
            self.assertIn("base_plan_fingerprint", plan_schema["properties"])
            self.assertIn("removed_session_ids", plan_schema["properties"])
            self.assertIn("removed_exercise_ids", plan_schema["properties"])
            self.assertNotIn("valid_from", plan_schema["properties"])
            self.assertNotIn(
                "anyOf",
                plan_schema["properties"]["replaces_plan_id"],
            )
            self.assertEqual(
                plan_schema["properties"]["sessions"]["maxItems"], 14
            )
            session_schema = plan_schema["properties"]["sessions"]["items"]
            self.assertEqual(
                session_schema["properties"]["exercises"]["maxItems"], 32
            )
            set_schema = session_schema["properties"]["exercises"]["items"][
                "properties"
            ]["sets"]
            exercise_schema = session_schema["properties"]["exercises"]["items"]
            self.assertEqual(set_schema["minItems"], 1)
            self.assertEqual(set_schema["maxItems"], 20)
            self.assertIn("exercise_id", exercise_schema["properties"])
            self.assertNotIn("note", exercise_schema["required"])
            self.assertNotIn(
                "anyOf",
                exercise_schema["properties"]["note"],
                "The published tool schema must not advertise explicit null notes.",
            )
            self.assertEqual(
                exercise_schema["properties"]["rest_seconds"]["maximum"],
                300,
            )
            self.assertNotIn(
                "anyOf",
                exercise_schema["properties"]["rest_seconds"],
                "The published tool schema must not advertise explicit null.",
            )
            self.assertNotIn("rest_seconds", exercise_schema["required"])
            self.assertIn(
                "reps_max",
                set_schema["items"]["properties"],
            )
            self.assertNotIn(
                "anyOf",
                set_schema["items"]["properties"]["reps_max"],
            )
            result = await client.call_tool(
                "propose_training_plan",
                {"plan": plan, "origin_ticket": ticket},
            )
            self.assertEqual(result.data["status"], "pending")

            replay = await client.call_tool(
                "propose_training_plan",
                {"plan": plan, "origin_ticket": ticket},
                raise_on_error=False,
            )
            self.assertTrue(replay.is_error)

        pending = self.store.pending_plans("private-mcp")
        self.assertEqual(len(pending), 1)
        self.assertEqual(
            pending[0]["origin"],
            {"version": 1, "conversation_id": conversation_id},
        )
        self.assertEqual(pending[0]["replaces_plan_id"], replacement_plan_id)
        self.assertEqual(
            pending[0]["base_plan_fingerprint"], "sha256:" + "a" * 64
        )
        self.assertEqual(pending[0]["removed_session_ids"], [])
        self.assertEqual(pending[0]["removed_exercise_ids"], [])
        self.assertEqual(pending[0]["sessions"][0]["cardio_target"], {})
        advanced = pending[0]["sessions"][1]["exercises"][0]
        self.assertEqual(advanced["exercise_id"], exercise_id)
        self.assertEqual(advanced["sets"][0]["reps_max"], 12)
        self.assertEqual(advanced["rest_seconds"], 180)
        self.assertEqual(advanced["warmup_sets"], 2)
        self.assertEqual(advanced["warmup_weight_kg"], 40.0)
        self.assertIs(advanced["bilateral"], False)
        self.assertNotIn(
            "note",
            pending[0]["sessions"][1]["exercises"][1],
        )
        self.assertEqual(
            pending[0]["sessions"][1]["exercises"][2]["note"],
            "",
        )

    def test_runtime_logging_is_clamped_after_fastmcp_was_imported(self) -> None:
        self.assertTrue(fastmcp.settings.log_enabled)
        self.assertEqual(fastmcp.settings.log_level, "ERROR")
        self.assertFalse(fastmcp.settings.enable_rich_tracebacks)
        self.assertFalse(fastmcp.settings.enable_rich_logging)
        self.assertFalse(fastmcp.settings.debug)
        for namespace in (
            "",
            "fastmcp",
            "fastmcp.server.server",
            "fastmcp.server.mixins.mcp_operations",
            "mcp",
            "mcp.server.lowlevel.server",
        ):
            logger = logging.getLogger(namespace)
            self.assertGreaterEqual(logger.getEffectiveLevel(), logging.ERROR)
            self.assertTrue(
                all(handler.level >= logging.ERROR for handler in logger.handlers)
            )
            self.assertFalse(
                any(isinstance(handler, RichHandler) for handler in logger.handlers)
            )

    def test_explicit_sub_error_log_levels_fail_closed_at_startup(self) -> None:
        for level in ("DEBUG", "INFO", "WARNING"):
            env = os.environ.copy()
            env["FASTMCP_LOG_LEVEL"] = level
            env["PYTHONPATH"] = os.pathsep.join(
                filter(None, (str(SERVER_ROOT), env.get("PYTHONPATH")))
            )
            completed = subprocess.run(
                [sys.executable, "-c", "import fastmcp; import mcp_server"],
                cwd=SERVER_ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
            with self.subTest(level=level):
                self.assertNotEqual(completed.returncode, 0)
                self.assertIn(
                    "FASTMCP_LOG_LEVEL must be ERROR or CRITICAL",
                    completed.stderr,
                )

    def test_missing_mcp_client_id_has_no_max_fallback(self) -> None:
        env = os.environ.copy()
        env.pop("KORE_MCP_CLIENT_ID", None)
        env["PYTHONPATH"] = os.pathsep.join(
            filter(None, (str(SERVER_ROOT), env.get("PYTHONPATH")))
        )

        completed = subprocess.run(
            [sys.executable, "-c", "import mcp_server"],
            cwd=SERVER_ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn(
            "required environment variable KORE_MCP_CLIENT_ID is missing",
            completed.stderr,
        )

    async def test_pydantic_and_fastmcp_reject_overlimit_before_store(self) -> None:
        ticket, _ = self.ticket()
        pending_before = len(self.store.pending_plans("private-mcp"))
        invalid = {
            "schema_version": "1.0",
            "name": "é" * 65,
            "sessions": [
                {
                    "name": "Strength",
                    "exercises": [
                        {
                            "name": "Squat",
                            "sets": [{"weight_kg": 60, "reps": 8}],
                        }
                    ],
                }
            ],
        }

        collector = _RecordCollector()
        captured_namespaces = tuple(
            logging.getLogger(namespace) for namespace in ("", "fastmcp", "mcp")
        )
        for logger in captured_namespaces:
            logger.addHandler(collector)
        raw_response_count = 0
        raw_responses_received = asyncio.Event()

        async def handle_raw_response(message: object) -> None:
            nonlocal raw_response_count
            if isinstance(message, RuntimeError) and "unknown request ID" in str(
                message
            ):
                raw_response_count += 1
                if raw_response_count == 2:
                    raw_responses_received.set()

        try:
            async with Client(
                self.mcp_server.mcp,
                message_handler=handle_raw_response,
            ) as client:
                result = await client.call_tool(
                    "propose_training_plan",
                    {"plan": invalid, "origin_ticket": ticket},
                    raise_on_error=False,
                )
                foundation_blank = {
                    "schema_version": "1.0",
                    "name": "\u200b",
                    "sessions": invalid["sessions"],
                }
                foundation_blank_result = await client.call_tool(
                    "propose_training_plan",
                    {"plan": foundation_blank, "origin_ticket": ticket},
                    raise_on_error=False,
                )
                private_plan_sentinel = {
                    "schema_version": "1.0",
                    "name": "private-plan-payload-sentinel",
                    "sessions": invalid["sessions"],
                }
                private_ticket_sentinel = "private-origin-ticket-sentinel"
                invalid_ticket_result = await client.call_tool(
                    "propose_training_plan",
                    {
                        "plan": private_plan_sentinel,
                        "origin_ticket": private_ticket_sentinel,
                    },
                    raise_on_error=False,
                )
                raw_arguments = (
                    {"origin_ticket": private_ticket_sentinel},
                    {"plan": private_plan_sentinel["name"]},
                )
                for request_id, arguments in enumerate(raw_arguments, start=900):
                    raw_request = JSONRPCRequest(
                        jsonrpc="2.0",
                        id=request_id,
                        method="tools/call",
                        params={"name": 123, "arguments": arguments},
                    )
                    await client.session._write_stream.send(
                        SessionMessage(message=JSONRPCMessage(raw_request))
                    )
                await asyncio.wait_for(raw_responses_received.wait(), timeout=1)
        finally:
            for logger in captured_namespaces:
                logger.removeHandler(collector)

        self.assertTrue(result.is_error)
        self.assertTrue(foundation_blank_result.is_error)
        self.assertTrue(invalid_ticket_result.is_error)
        self.assertEqual(raw_response_count, 2)
        formatter = logging.Formatter("%(levelname)s:%(name)s:%(message)s")
        rendered_logs = "\n".join(
            formatter.format(record) for record in collector.records
        )
        self.assertNotIn(ticket, rendered_logs)
        self.assertNotIn(invalid["name"], rendered_logs)
        self.assertNotIn("\u200b", rendered_logs)
        self.assertNotIn(private_ticket_sentinel, rendered_logs)
        self.assertNotIn(private_plan_sentinel["name"], rendered_logs)
        self.assertTrue(
            all(record.levelno >= logging.ERROR for record in collector.records)
        )
        self.assertEqual(
            len(self.store.pending_plans("private-mcp")), pending_before
        )

    async def test_live_read_tool_behavior_is_preserved(self) -> None:
        self.store.save_snapshot(
            {
                "generated_at": "2026-07-14T12:00:00Z",
                "recovery": {
                    "date": "2026-07-14",
                    "readiness": 82,
                    "hrv_sdnn_ms": 55,
                    "hrv_baseline_ms": 50,
                    "hrv_method_effective": "sdnnFallbackFromRMSSD",
                    "hrv_kind": "sdnn",
                    "hrv_source": "sleepHealthKitSDNN",
                    "hrv_quality": "fallback",
                    "hrv_sample_count": 3,
                    "hrv_contributed_to_recovery": False,
                    "hrv_fallback_reason": "No valid rMSSD series.",
                },
                "hrv_trend": [{"date": "2026-07-14", "sdnn_ms": 55}],
                "sleep": [],
                "workouts": [],
                "profile": None,
                "active_plan_name": None,
                "plans": None,
            }
        )

        async with Client(self.mcp_server.mcp) as client:
            recovery = await client.call_tool("get_recovery_today")
            plans = await client.call_tool("get_training_plans")
            briefing = await client.call_tool("get_coach_briefing")

        self.assertEqual(recovery.data["readiness"], 82)
        self.assertEqual(recovery.data["hrv_sdnn_ms"], 55)
        self.assertEqual(
            recovery.data["hrv_method_effective"], "sdnnFallbackFromRMSSD"
        )
        self.assertEqual(recovery.data["hrv_kind"], "sdnn")
        self.assertEqual(recovery.data["hrv_source"], "sleepHealthKitSDNN")
        self.assertEqual(recovery.data["hrv_quality"], "fallback")
        self.assertEqual(recovery.data["hrv_sample_count"], 3)
        self.assertFalse(recovery.data["hrv_contributed_to_recovery"])
        self.assertEqual(briefing.data["recovery_today"], recovery.data)
        self.assertEqual(plans.data["plans"], [])
        self.assertEqual(
            plans.data["snapshot_generated_at"], "2026-07-14T12:00:00Z"
        )
        self.assertIsInstance(plans.data["snapshot_received_at"], float)
        self.assertEqual(
            briefing.data["snapshot_generated_at"], "2026-07-14T12:00:00Z"
        )
        self.assertEqual(
            briefing.data["snapshot_received_at"],
            plans.data["snapshot_received_at"],
        )


if __name__ == "__main__":
    unittest.main()
