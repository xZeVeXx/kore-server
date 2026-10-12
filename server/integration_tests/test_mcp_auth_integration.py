"""MCP bearer check without FastMCP's StaticTokenVerifier (0.13 S11b).

`StaticTokenVerifier` looks the presented token up in a dict ("Never use this in production" is
its own docstring). `ConstantTimeTokenVerifier` hashes both sides and compares with
`hmac.compare_digest`.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import secrets
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import fastmcp
from fastmcp.server.auth.providers.jwt import StaticTokenVerifier
from fastapi.testclient import TestClient


SERVER_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_ROOT))

_FRESH_MODULES = ("mcp_server", "store", "models", "origin_ticket")
_ENV_KEYS = (
    "KORE_DB_PATH", "KORE_INSTANCE_ID", "KORE_MCP_TOKEN", "KORE_MCP_CLIENT_ID",
    "KORE_PLAN_ORIGIN_PRINCIPAL", "KORE_PLAN_ORIGIN_SECRET", "FASTMCP_LOG_LEVEL",
    "KORE_MCP_ALLOWED_ORIGINS", "FASTMCP_CHECK_FOR_UPDATES", "KORE_MCP_HOST", "KORE_MCP_PORT",
)


class _McpEnvironment(unittest.TestCase):
    """A fresh `mcp_server` import with its own token; no tests of its own."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.token = secrets.token_urlsafe(32)
        cls._saved_env = {key: os.environ.get(key) for key in _ENV_KEYS}
        os.environ.update({
            "KORE_DB_PATH": str(Path(cls.temp_dir.name) / "mcp-auth.db"),
            "KORE_INSTANCE_ID": "mcp-auth",
            "KORE_MCP_TOKEN": cls.token,
            "KORE_MCP_CLIENT_ID": "auth-client",
            "KORE_PLAN_ORIGIN_PRINCIPAL": "mcp-auth",
            "KORE_PLAN_ORIGIN_SECRET": secrets.token_urlsafe(48),
            "FASTMCP_LOG_LEVEL": "ERROR",
        })
        os.environ.pop("KORE_MCP_ALLOWED_ORIGINS", None)
        for name in _FRESH_MODULES:
            sys.modules.pop(name, None)
        cls.mcp_server = importlib.import_module("mcp_server")

    @classmethod
    def tearDownClass(cls) -> None:
        for key, value in cls._saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        for name in _FRESH_MODULES:
            sys.modules.pop(name, None)
        cls.temp_dir.cleanup()


class ConstantTimeTokenVerifierTests(_McpEnvironment):
    def verify(self, token: str):
        return asyncio.run(self.mcp_server.token_verifier.verify_token(token))

    def test_the_server_does_not_use_the_dict_based_static_verifier(self) -> None:
        verifier = self.mcp_server.token_verifier
        self.assertNotIsInstance(verifier, StaticTokenVerifier)
        self.assertIs(self.mcp_server.mcp.auth, verifier)
        # The configured token is not a dict key (or any attribute) of the verifier.
        self.assertFalse(any(
            isinstance(value, dict) and self.token in value for value in vars(verifier).values()
        ))
        self.assertNotIn(self.token, repr(vars(verifier)))

    def test_the_configured_token_yields_the_client_and_the_scopes_of_before(self) -> None:
        access = self.verify(self.token)
        self.assertIsNotNone(access)
        self.assertEqual(access.token, self.token)
        self.assertEqual(access.client_id, "auth-client")
        self.assertEqual(access.scopes, ["read:health", "write:plan"])
        self.assertIsNone(access.expires_at)
        self.assertEqual(self.mcp_server.token_verifier.required_scopes, ["read:health"])

    def test_every_other_value_is_refused(self) -> None:
        for value in ("", " ", self.token[:-1], self.token + "x", self.token.upper(),
                      " " + self.token, self.token + " ", "Bearer " + self.token,
                      "q" * 4096, "töken", "\ud800"):
            with self.subTest(value=value[:12]):
                self.assertIsNone(self.verify(value))

    def test_the_comparison_is_hmac_compare_digest_on_equal_length_digests(self) -> None:
        calls = []
        real = self.mcp_server.hmac.compare_digest

        def spy(left, right):
            calls.append((left, right))
            return real(left, right)

        with mock.patch.object(self.mcp_server.hmac, "compare_digest", spy):
            self.assertIsNone(self.verify("short"))
            self.assertIsNotNone(self.verify(self.token))
        self.assertEqual(len(calls), 2)
        for left, right in calls:
            self.assertEqual(len(left), len(right), "digests, so token length does not leak")
            self.assertNotIn(self.token.encode(), (left, right))


class McpHttpAuthTests(_McpEnvironment):
    """The same verifier, reached through the HTTP app the way `run()` builds it."""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.app = cls.mcp_server.mcp.http_app(middleware=cls.mcp_server.http_middleware())
        cls.client_context = TestClient(cls.app)
        cls.client = cls.client_context.__enter__()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client_context.__exit__(None, None, None)
        super().tearDownClass()

    def test_no_wrong_and_right_token_over_http(self) -> None:
        self.assertEqual(self.client.post("/mcp/", json={}).status_code, 401)
        wrong = self.client.post("/mcp/", json={}, headers={"Authorization": "Bearer " + "z" * 43})
        self.assertEqual(wrong.status_code, 401)
        # Authenticated: the empty JSON object is then a protocol error, not an auth error.
        right = self.client.post(
            "/mcp/", json={}, headers={"Authorization": f"Bearer {self.token}"}
        )
        self.assertNotIn(right.status_code, (401, 403))

    def test_an_older_mcp_client_can_handshake_and_list_the_tools_with_the_right_token(self) -> None:
        # Hermes speaks a handshake-era MCP protocol: initialize, the initialized notification,
        # then tools/list on its session. What the server answers must not change with the
        # FastMCP / MCP SDK version.
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json, text/event-stream",
        }

        def reply(response):
            self.assertEqual(response.status_code, 200)
            data = [line[5:] for line in response.text.splitlines() if line.startswith("data:")]
            return json.loads(data[-1] if data else response.text)

        for version in ("2025-03-26", "2025-06-18"):
            with self.subTest(protocol_version=version):
                initialize = self.client.post("/mcp", headers=headers, json={
                    "jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"protocolVersion": version, "capabilities": {},
                               "clientInfo": {"name": "hermes-like", "version": "1"}},
                })
                result = reply(initialize)["result"]
                self.assertEqual(result["protocolVersion"], version)
                session = {
                    **headers,
                    "mcp-session-id": initialize.headers["mcp-session-id"],
                    "mcp-protocol-version": version,
                }
                accepted = self.client.post("/mcp", headers=session, json={
                    "jsonrpc": "2.0", "method": "notifications/initialized",
                })
                self.assertEqual(accepted.status_code, 202)
                tools = reply(self.client.post("/mcp", headers=session, json={
                    "jsonrpc": "2.0", "id": 2, "method": "tools/list",
                }))["result"]["tools"]
                names = [tool["name"] for tool in tools]
                self.assertIn("get_coach_briefing", names)
                self.assertIn("propose_training_plan", names)

    def test_two_authorization_headers_are_refused_before_the_body_is_read(self) -> None:
        # 0.13 S14, like bridge and ingest: a duplicate is ambiguous, not "the first one wins"
        # (FastMCP's bearer backend reads only the first).
        valid = (b"authorization", f"Bearer {self.token}".encode())
        for second in (valid, (b"authorization", b"Bearer other"), (b"Authorization", b"")):
            with self.subTest(second=second[1][:12]):
                calls = []

                async def receive():
                    calls.append(1)
                    return {"type": "http.request", "body": b"{}", "more_body": False}

                sent = []

                async def send(message):
                    sent.append(message)

                scope = {
                    "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                    "method": "POST", "path": "/mcp/", "raw_path": b"/mcp/", "query_string": b"",
                    "root_path": "", "scheme": "http", "server": ("127.0.0.1", 8000),
                    "client": ("127.0.0.1", 50000),
                    "headers": [valid, second, (b"content-type", b"application/json"),
                                (b"content-length", b"2")],
                }
                asyncio.run(self.app(scope, receive, send))
                start = next(m for m in sent if m["type"] == "http.response.start")
                self.assertEqual(start["status"], 401)
                self.assertEqual(calls, [], "the body must stay unread")

    def test_two_authorization_headers_over_http(self) -> None:
        response = self.client.post("/mcp/", json={}, headers=[
            ("Authorization", f"Bearer {self.token}"), ("Authorization", f"Bearer {self.token}"),
        ])
        self.assertEqual(response.status_code, 401)



class McpServeTests(_McpEnvironment):
    """How `python mcp_server.py` starts uvicorn (0.13 S14): connection limits, no update check."""

    @classmethod
    def setUpClass(cls) -> None:
        # Asked for by the environment or a previous import: the server still turns it off.
        fastmcp.settings.check_for_updates = "stable"
        super().setUpClass()

    def test_the_fastmcp_update_check_is_off(self) -> None:
        # FastMCP asks PyPI for a newer version when it prints its banner unless
        # FASTMCP_CHECK_FOR_UPDATES is "off" (fastmcp/settings.py, utilities/version_check.py).
        self.assertEqual(fastmcp.settings.check_for_updates, "off")
        self.assertEqual(os.environ["FASTMCP_CHECK_FOR_UPDATES"], "off")

    def test_serve_hands_uvicorn_the_limits_and_makes_no_outbound_call(self) -> None:
        captured = {}

        async def fake_serve(server, sockets=None):
            captured["config"] = server.config

        os.environ.pop("KORE_MCP_HOST", None)
        os.environ["KORE_MCP_PORT"] = "8000"
        with mock.patch("uvicorn.Server.serve", fake_serve), mock.patch(
            "fastmcp.utilities.version_check.get_latest_version"
        ) as latest:
            self.mcp_server.serve()
        latest.assert_not_called()
        config = captured["config"]
        self.assertEqual((config.host, config.port), ("127.0.0.1", 8000))
        self.assertEqual(config.limit_concurrency, 1024)
        self.assertEqual(config.timeout_keep_alive, 5)


if __name__ == "__main__":
    unittest.main()
