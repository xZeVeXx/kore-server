"""Ingest und MCP lauschen nur auf Loopback, RFC 1918 oder 100.64.0.0/10 (0.13 S11b).

Wie bei der Bridge: konkrete IPv4-Adresse, nie Wildcard, nie Hostname. Nur im Docker-Container
(Compose setzt KORE_PUBLISHED_BIND) darf der Prozess selbst auf 0.0.0.0 lauschen; dann zaehlt die
veroeffentlichte Host-Adresse und wird genauso geprueft.
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

from bind_guard import PUBLISHED_BIND_ENV, listen_host, validated_bind_host  # noqa: E402


class ValidatedBindHostTests(unittest.TestCase):
    def test_loopback_private_and_vpn_addresses_pass(self) -> None:
        for value in ("127.0.0.1", "127.1.2.3", "10.0.0.10", "172.16.0.5", "172.31.255.254",
                      "192.168.1.20", "100.64.0.10", "100.127.255.254"):
            with self.subTest(value=value):
                self.assertEqual(validated_bind_host(value, "KORE_BIND_ADDRESS"), value)

    def test_wildcards_public_and_odd_addresses_are_refused_by_key_name(self) -> None:
        for value in ("0.0.0.0", "::", "::1", "[::1]", "", " ", "localhost", "example.com",
                      "8.8.8.8", "192.0.2.1", "172.32.0.1", "100.128.0.1", "100.63.255.255",
                      "169.254.1.1", "10.0.0.256", "10.0.0", " 10.0.0.10", "10.0.0.10 ",
                      "10.0.0.10:8081", "fe80::1", "::ffff:10.0.0.10", "010.000.000.010"):
            with self.subTest(value=value):
                with self.assertRaises(RuntimeError) as raised:
                    validated_bind_host(value, "KORE_BIND_ADDRESS")
                self.assertIn("KORE_BIND_ADDRESS", str(raised.exception))

    def test_non_strings_are_refused(self) -> None:
        for value in (None, 10, b"10.0.0.10"):
            with self.subTest(value=value):
                with self.assertRaises(RuntimeError):
                    validated_bind_host(value, "KORE_BIND_ADDRESS")


class ListenHostTests(unittest.TestCase):
    def test_without_docker_the_listen_address_itself_is_validated(self) -> None:
        env = {"KORE_MCP_HOST": "10.0.0.10"}
        self.assertEqual(listen_host("KORE_MCP_HOST", env), "10.0.0.10")

    def test_the_default_is_loopback_also_for_an_empty_value(self) -> None:
        self.assertEqual(listen_host("KORE_MCP_HOST", {}), "127.0.0.1")
        self.assertEqual(listen_host("KORE_MCP_HOST", {"KORE_MCP_HOST": ""}), "127.0.0.1")

    def test_a_fallback_variable_is_used_when_the_first_is_unset(self) -> None:
        env = {"KORE_BIND_ADDRESS": "100.64.0.10"}
        self.assertEqual(listen_host("KORE_INGEST_HOST", env, fallback="KORE_BIND_ADDRESS"), "100.64.0.10")
        env["KORE_INGEST_HOST"] = "127.0.0.1"
        self.assertEqual(listen_host("KORE_INGEST_HOST", env, fallback="KORE_BIND_ADDRESS"), "127.0.0.1")

    def test_a_wildcard_is_refused_outside_docker(self) -> None:
        for value in ("0.0.0.0", "::"):
            with self.subTest(value=value):
                with self.assertRaises(RuntimeError) as raised:
                    listen_host("KORE_MCP_HOST", {"KORE_MCP_HOST": value})
                self.assertIn("KORE_MCP_HOST", str(raised.exception))
                self.assertIn(PUBLISHED_BIND_ENV, str(raised.exception))

    def test_a_wrong_fallback_value_is_named_by_its_own_key(self) -> None:
        with self.assertRaises(RuntimeError) as raised:
            listen_host("KORE_INGEST_HOST", {"KORE_BIND_ADDRESS": "0.0.0.0"}, fallback="KORE_BIND_ADDRESS")
        self.assertIn("KORE_BIND_ADDRESS", str(raised.exception))

    def test_inside_docker_the_wildcard_is_allowed_when_the_published_address_is_valid(self) -> None:
        for published in ("127.0.0.1", "10.0.0.10", "100.64.0.10"):
            with self.subTest(published=published):
                env = {"KORE_MCP_HOST": "0.0.0.0", PUBLISHED_BIND_ENV: published}
                self.assertEqual(listen_host("KORE_MCP_HOST", env), "0.0.0.0")

    def test_inside_docker_a_wildcard_or_public_published_address_stops_the_start(self) -> None:
        for published in ("0.0.0.0", "::", "", "8.8.8.8", "example.com"):
            with self.subTest(published=published):
                env = {"KORE_MCP_HOST": "0.0.0.0", PUBLISHED_BIND_ENV: published}
                with self.assertRaises(RuntimeError) as raised:
                    listen_host("KORE_MCP_HOST", env)
                self.assertIn(PUBLISHED_BIND_ENV, str(raised.exception))

    def test_a_published_address_does_not_unlock_other_wildcards(self) -> None:
        env = {"KORE_MCP_HOST": "::", PUBLISHED_BIND_ENV: "127.0.0.1"}
        with self.assertRaises(RuntimeError):
            listen_host("KORE_MCP_HOST", env)

    def test_inside_docker_with_nothing_configured_the_default_stays_loopback(self) -> None:
        self.assertEqual(listen_host("KORE_MCP_HOST", {PUBLISHED_BIND_ENV: "127.0.0.1"}), "127.0.0.1")


class ServiceStartTests(unittest.TestCase):
    """`python ingest_api.py` / `python mcp_server.py` stop before they ever open a socket."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)

    def env(self, **extra: str) -> dict[str, str]:
        env = os.environ.copy()
        for key in ("KORE_BIND_ADDRESS", "KORE_INGEST_HOST", "KORE_MCP_HOST", PUBLISHED_BIND_ENV):
            env.pop(key, None)
        env.update({
            "KORE_DB_PATH": str(Path(self.temp_dir.name) / "bind.db"),
            "KORE_INSTANCE_ID": "bind-test",
            "KORE_PLAN_ORIGIN_PRINCIPAL": "bind-test",
            "KORE_INGEST_TOKEN": secrets.token_hex(24),
            "KORE_MCP_TOKEN": secrets.token_hex(24),
            "KORE_MCP_CLIENT_ID": "bind-test",
            "KORE_PLAN_ORIGIN_SECRET": secrets.token_hex(32),
            "FASTMCP_LOG_LEVEL": "ERROR",
        })
        env.update(extra)
        return env

    def run_script(self, script: str, env: dict[str, str]) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, script], cwd=SERVER_ROOT, env=env,
            capture_output=True, text=True, timeout=30, check=False,
        )

    def test_ingest_refuses_a_wildcard_bind_address(self) -> None:
        completed = self.run_script("ingest_api.py", self.env(KORE_BIND_ADDRESS="0.0.0.0"))
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("KORE_BIND_ADDRESS", completed.stderr)

    def test_ingest_refuses_a_public_bind_address(self) -> None:
        completed = self.run_script("ingest_api.py", self.env(KORE_BIND_ADDRESS="8.8.8.8"))
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("KORE_BIND_ADDRESS", completed.stderr)

    def test_ingest_in_docker_refuses_a_wildcard_published_address(self) -> None:
        completed = self.run_script(
            "ingest_api.py",
            self.env(KORE_INGEST_HOST="0.0.0.0", **{PUBLISHED_BIND_ENV: "0.0.0.0"}),
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn(PUBLISHED_BIND_ENV, completed.stderr)

    def test_mcp_refuses_a_wildcard_host(self) -> None:
        completed = self.run_script("mcp_server.py", self.env(KORE_MCP_HOST="0.0.0.0"))
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("KORE_MCP_HOST", completed.stderr)

    def test_mcp_in_docker_refuses_a_public_published_address(self) -> None:
        completed = self.run_script(
            "mcp_server.py",
            self.env(KORE_MCP_HOST="0.0.0.0", **{PUBLISHED_BIND_ENV: "203.0.113.7"}),
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn(PUBLISHED_BIND_ENV, completed.stderr)


if __name__ == "__main__":
    unittest.main()
