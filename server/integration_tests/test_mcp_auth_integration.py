"""MCP bearer check without FastMCP's StaticTokenVerifier (0.13 S11b).

`StaticTokenVerifier` looks the presented token up in a dict ("Never use this in production" is
its own docstring). `ConstantTimeTokenVerifier` hashes both sides and compares with
`hmac.compare_digest`.
"""

from __future__ import annotations

import asyncio
import importlib
import os
import secrets
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastmcp.server.auth.providers.jwt import StaticTokenVerifier
from fastapi.testclient import TestClient


SERVER_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_ROOT))

_FRESH_MODULES = ("mcp_server", "store", "models", "origin_ticket")
_ENV_KEYS = (
    "KORE_DB_PATH", "KORE_INSTANCE_ID", "KORE_MCP_TOKEN", "KORE_MCP_CLIENT_ID",
    "KORE_PLAN_ORIGIN_PRINCIPAL", "KORE_PLAN_ORIGIN_SECRET", "FASTMCP_LOG_LEVEL",
    "KORE_MCP_ALLOWED_ORIGINS",
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
        cls.app = cls.mcp_server.mcp.http_app(middleware=cls.mcp_server.origin_guard_middleware())
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


if __name__ == "__main__":
    unittest.main()
