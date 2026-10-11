"""Die Beispiel-Units binden nicht auf 0.0.0.0 und warten aufs Netz."""

from __future__ import annotations

import configparser
import re
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
UNITS = (
    REPO_ROOT / "deploy" / "kore-ingest.service",
    REPO_ROOT / "deploy" / "kore-mcp.service",
    REPO_ROOT / "coach-bridge" / "kore-coach-bridge.service",
)


def _read_unit(path: Path) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str
    parser.read_string(path.read_text(encoding="utf-8"))
    return parser


class DeployUnitTests(unittest.TestCase):
    def test_units_wait_for_the_network_and_keep_restarting(self) -> None:
        for path in UNITS:
            with self.subTest(unit=path.name):
                unit = _read_unit(path)
                self.assertEqual(unit["Unit"].get("Wants"), "network-online.target")
                self.assertEqual(unit["Unit"].get("After"), "network-online.target")
                self.assertEqual(unit["Unit"].get("StartLimitIntervalSec"), "0")
                self.assertEqual(unit["Service"].get("RestartSec"), "5")

    def test_no_unit_binds_to_every_interface(self) -> None:
        for path in UNITS:
            with self.subTest(unit=path.name):
                self.assertNotIn("0.0.0.0", path.read_text(encoding="utf-8"))

    def test_ingest_binds_to_the_configured_address_with_loopback_default(self) -> None:
        service = _read_unit(UNITS[0])["Service"]
        # ingest_api.py validates KORE_BIND_ADDRESS (loopback, RFC 1918, 100.64.0.0/10) before it
        # opens a socket; the unit no longer hands the value to a bare `uvicorn --host`.
        self.assertEqual(service["ExecStart"],
                         "/opt/kore-server/.venv/bin/python /opt/kore-server/ingest_api.py")
        self.assertNotIn("--host", service["ExecStart"])
        self.assertEqual(service.get("Environment"), "KORE_BIND_ADDRESS=127.0.0.1")

    def test_mcp_starts_through_its_own_bind_validation(self) -> None:
        service = _read_unit(UNITS[1])["Service"]
        self.assertEqual(service["ExecStart"], "/opt/kore-server/.venv/bin/python /opt/kore-server/mcp_server.py")

    def test_mcp_unit_turns_the_fastmcp_update_check_off(self) -> None:
        # No outbound call to PyPI at start (mcp_server.py forces it off as well).
        self.assertEqual(_read_unit(UNITS[1])["Service"].get("Environment"),
                         "FASTMCP_CHECK_FOR_UPDATES=off")

    def test_server_units_are_sandboxed_like_the_bridge_unit(self) -> None:
        common = {
            "NoNewPrivileges": "yes", "PrivateTmp": "yes", "ProtectSystem": "strict",
            "ReadWritePaths": "/opt/kore-server", "ProtectKernelTunables": "yes",
            "ProtectKernelModules": "yes", "ProtectControlGroups": "yes",
            "RestrictAddressFamilies": "AF_INET AF_INET6 AF_UNIX", "RestrictNamespaces": "yes",
            "RestrictSUIDSGID": "yes", "LockPersonality": "yes", "CapabilityBoundingSet": "",
            "UMask": "0077", "TasksMax": "128", "LimitNOFILE": "4096",
        }
        specific = {
            # Ingest reads devices.json from the bridge user's state dir, usually below /home.
            UNITS[0]: {"ProtectHome": "read-only", "MemoryMax": "768M"},
            UNITS[1]: {"ProtectHome": "yes", "MemoryMax": "384M"},
        }
        for path, extra in specific.items():
            service = _read_unit(path)["Service"]
            for key, value in {**common, **extra}.items():
                with self.subTest(unit=path.name, key=key):
                    self.assertEqual(service.get(key), value)

    def test_no_unit_allows_privilege_escalation(self) -> None:
        for path in UNITS:
            with self.subTest(unit=path.name):
                self.assertEqual(_read_unit(path)["Service"].get("NoNewPrivileges"), "yes")

    def test_bridge_unit_names_no_concrete_user_home_or_profile(self) -> None:
        text = UNITS[2].read_text(encoding="utf-8")
        for pattern in (r"^User=(?!<)\S+", r"^Group=(?!<)\S+", r"/home/(?!<)\w+"):
            with self.subTest(pattern=pattern):
                self.assertIsNone(re.search(pattern, text, re.MULTILINE))

    def test_bridge_unit_is_hardened_and_may_write_the_pairing_state(self) -> None:
        service = _read_unit(UNITS[2])["Service"]
        self.assertTrue(service["ExecStart"].endswith("<HOME>/kore-server/coach-bridge/bridge.py"))
        self.assertEqual(service.get("EnvironmentFile"), "<HOME>/kore-coach-bridge/bridge.env")
        for key, value in (("ProtectSystem", "strict"), ("PrivateTmp", "yes"), ("UMask", "0077")):
            with self.subTest(key=key):
                self.assertEqual(service.get(key), value)
        writable = service["ReadWritePaths"].split()
        for path in ("<HOME>/.hermes", "<HOME>/kore-server/state", "-<HOME>/.cache"):
            with self.subTest(path=path):
                self.assertIn(path, writable)

    def test_readme_documents_the_unit_that_ships_in_the_repo(self) -> None:
        directives = {line for line in UNITS[2].read_text(encoding="utf-8").splitlines()
                      if re.match(r"^[A-Za-z]+=", line)}
        for name in ("README.md", "README.de.md"):
            text = (REPO_ROOT / name).read_text(encoding="utf-8")
            for line in directives:
                with self.subTest(readme=name, directive=line):
                    self.assertIn(line, text)


if __name__ == "__main__":
    unittest.main()
