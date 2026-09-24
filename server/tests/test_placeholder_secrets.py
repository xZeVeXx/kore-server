"""S3: Ingest und MCP starten nicht mit leerem oder `change-me`-Geheimnis aus `.env.example`.

Die Meldung nennt den Schluessel, nie den Wert — sie landet im Journal des Dienstes.
"""

from __future__ import annotations

import os
import secrets
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SERVER_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_ROOT))

from placeholder_guard import reject_placeholder  # noqa: E402


EXAMPLE_PLACEHOLDERS = {
    "KORE_INGEST_TOKEN": "change-me-app-token",
    "KORE_MCP_TOKEN": "change-me-agent-token",
    "KORE_PLAN_ORIGIN_SECRET": "change-me-origin-secret-at-least-32-bytes-long",
}


class RejectPlaceholderTests(unittest.TestCase):
    def test_empty_missing_and_example_values_are_rejected_by_key_name(self) -> None:
        for value in (None, "", "   ", "change-me", "change-me-app-token", "CHANGE-ME-x"):
            with self.subTest(value=value):
                with self.assertRaises(RuntimeError) as raised:
                    reject_placeholder("KORE_INGEST_TOKEN", value)
                message = str(raised.exception)
                self.assertIn("KORE_INGEST_TOKEN", message)
                if value and value.strip():
                    self.assertNotIn(value, message)

    def test_a_real_value_passes(self) -> None:
        self.assertIsNone(reject_placeholder("KORE_MCP_TOKEN", secrets.token_hex(24)))


class ServiceStartupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)

    def valid_env(self) -> dict[str, str]:
        env = os.environ.copy()
        env.update(
            {
                "KORE_DB_PATH": str(Path(self.temp_dir.name) / "placeholder.db"),
                "KORE_INSTANCE_ID": "placeholder-test",
                "KORE_PLAN_ORIGIN_PRINCIPAL": "placeholder-test",
                "KORE_INGEST_TOKEN": secrets.token_hex(24),
                "KORE_MCP_TOKEN": secrets.token_hex(24),
                "KORE_MCP_CLIENT_ID": "placeholder-test",
                "KORE_PLAN_ORIGIN_SECRET": secrets.token_hex(32),
                "PYTHONPATH": os.pathsep.join(
                    filter(None, (str(SERVER_ROOT), os.environ.get("PYTHONPATH")))
                ),
            }
        )
        return env

    def start(self, module: str, env: dict[str, str]) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-c", f"import {module}"],
            cwd=SERVER_ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

    def assert_refuses(self, module: str, key: str, value: str) -> None:
        env = self.valid_env()
        env[key] = value
        completed = self.start(module, env)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn(key, completed.stderr)
        if value:
            self.assertNotIn(value, completed.stderr + completed.stdout)

    def test_valid_configuration_starts(self) -> None:
        for module in ("ingest_api", "mcp_server"):
            with self.subTest(module=module):
                completed = self.start(module, self.valid_env())
                self.assertEqual(completed.returncode, 0, completed.stderr[-500:])

    def test_ingest_refuses_empty_or_example_token(self) -> None:
        for value in ("", EXAMPLE_PLACEHOLDERS["KORE_INGEST_TOKEN"]):
            with self.subTest(value=value):
                self.assert_refuses("ingest_api", "KORE_INGEST_TOKEN", value)

    def test_mcp_refuses_empty_or_example_token_and_origin_secret(self) -> None:
        for key in ("KORE_MCP_TOKEN", "KORE_PLAN_ORIGIN_SECRET"):
            for value in ("", EXAMPLE_PLACEHOLDERS[key]):
                with self.subTest(key=key, value=value):
                    self.assert_refuses("mcp_server", key, value)


if __name__ == "__main__":
    unittest.main()
