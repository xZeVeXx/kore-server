"""The Origin guard on both live apps, not on a stand-in.

A unit test proves the policy. These prove the wiring: that the middleware really sits in
front of the FastAPI ingest app and in front of the app FastMCP assembles, and that it
runs *outside* the token check rather than after it.
"""

from __future__ import annotations

import importlib
import os
import secrets
import sys
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

SERVER_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_ROOT))

_FRESH_MODULES = ("ingest_api", "mcp_server", "store", "models", "origin_ticket", "origin_guard")


class IngestOriginGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.token = secrets.token_urlsafe(32)
        os.environ.update(
            {
                "KORE_DB_PATH": str(Path(cls.temp_dir.name) / "origin.db"),
                "KORE_INSTANCE_ID": "origin-integration",
                "KORE_INGEST_TOKEN": cls.token,
                "KORE_PLAN_ORIGIN_PRINCIPAL": "private-origin",
                "KORE_MCP_ALLOWED_ORIGINS": "http://localhost:5173",
            }
        )
        for name in _FRESH_MODULES:
            sys.modules.pop(name, None)
        cls.ingest_api = importlib.import_module("ingest_api")
        cls.client_context = TestClient(cls.ingest_api.app)
        cls.client = cls.client_context.__enter__()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client_context.__exit__(None, None, None)
        cls.temp_dir.cleanup()
        os.environ.pop("KORE_MCP_ALLOWED_ORIGINS", None)
        for name in _FRESH_MODULES:
            sys.modules.pop(name, None)

    def authorization(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    def test_the_app_reads_the_allowlist_from_the_environment_at_import(self) -> None:
        self.assertEqual(self.ingest_api.ALLOWED_ORIGINS, frozenset({"http://localhost:5173"}))

    def test_health_stays_open_for_a_client_that_sends_no_origin(self) -> None:
        self.assertEqual(self.client.get("/health").json(), {"ok": True})

    def test_health_is_closed_to_a_foreign_browser_origin(self) -> None:
        # /health needs no token, so without the guard it is a free existence oracle for
        # any page the user has open.
        response = self.client.get("/health", headers={"Origin": "http://evil.example"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json(), {"detail": "Forbidden origin"})

    def test_the_allowlisted_origin_reaches_the_route(self) -> None:
        response = self.client.get("/health", headers={"Origin": "http://localhost:5173"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True})

    def test_a_foreign_origin_is_refused_even_with_the_correct_token(self) -> None:
        # The guard must be the outer layer: a rebinding attack that somehow learned the
        # token still gets 403, not 200.
        response = self.client.get(
            "/plan/pending",
            headers={**self.authorization(), "Origin": "http://evil.example"},
        )
        self.assertEqual(response.status_code, 403)

    def test_a_foreign_origin_is_refused_before_the_token_is_even_checked(self) -> None:
        # Without a token this route answers 401. With a bad origin it must answer 403
        # instead — otherwise the guard runs after authentication and the response still
        # distinguishes "wrong token" from "no such route".
        response = self.client.get("/plan/pending", headers={"Origin": "http://evil.example"})
        self.assertEqual(response.status_code, 403)

    def test_a_foreign_origin_cannot_post_a_snapshot(self) -> None:
        response = self.client.post(
            "/ingest",
            json={"generated_at": "2026-09-17T06:00:00Z"},
            headers={**self.authorization(), "Origin": "http://evil.example"},
        )
        self.assertEqual(response.status_code, 403)


class MCPOriginGuardTests(unittest.TestCase):
    """The same guard on the app FastMCP builds, reached the way `run()` reaches it."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        os.environ.update(
            {
                "KORE_DB_PATH": str(Path(cls.temp_dir.name) / "mcp-origin.db"),
                "KORE_INSTANCE_ID": "mcp-origin-integration",
                "KORE_MCP_TOKEN": secrets.token_urlsafe(32),
                "KORE_MCP_CLIENT_ID": "origin-integration",
                "KORE_PLAN_ORIGIN_PRINCIPAL": "private-mcp-origin",
                "KORE_PLAN_ORIGIN_SECRET": secrets.token_urlsafe(48),
                "KORE_MCP_ALLOWED_ORIGINS": "http://localhost:5173",
                "FASTMCP_LOG_LEVEL": "ERROR",
            }
        )
        for name in _FRESH_MODULES:
            sys.modules.pop(name, None)
        cls.mcp_server = importlib.import_module("mcp_server")
        # Exactly the middleware list that `mcp.run(transport="http", ...)` is given.
        cls.app = cls.mcp_server.mcp.http_app(
            middleware=cls.mcp_server.http_middleware()
        )
        cls.client_context = TestClient(cls.app)
        cls.client = cls.client_context.__enter__()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client_context.__exit__(None, None, None)
        cls.temp_dir.cleanup()
        os.environ.pop("KORE_MCP_ALLOWED_ORIGINS", None)
        for name in _FRESH_MODULES:
            sys.modules.pop(name, None)

    def test_the_mcp_route_is_still_token_protected_without_an_origin(self) -> None:
        # Unchanged behaviour for the hermes CLI: no Origin header, so the bearer token
        # remains the only thing standing between a caller and the tools.
        response = self.client.post("/mcp/", json={})
        self.assertEqual(response.status_code, 401)

    def test_a_foreign_origin_is_refused_by_the_guard_not_by_the_token(self) -> None:
        response = self.client.post("/mcp/", json={}, headers={"Origin": "http://evil.example"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json(), {"detail": "Forbidden origin"})

    def test_the_allowlisted_origin_passes_the_guard_and_meets_the_token_check(self) -> None:
        # Past the guard the request is a normal unauthenticated MCP call again: 401, not 403.
        response = self.client.post("/mcp/", json={}, headers={"Origin": "http://localhost:5173"})
        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
