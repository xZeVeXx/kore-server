"""S2 und S7: Die Beispiel-Units binden nicht auf 0.0.0.0 und warten aufs Netz."""

from __future__ import annotations

import configparser
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
        self.assertIn("--host ${KORE_BIND_ADDRESS}", service["ExecStart"])
        self.assertEqual(service.get("Environment"), "KORE_BIND_ADDRESS=127.0.0.1")

    def test_bridge_unit_names_no_concrete_user_home_or_profile(self) -> None:
        text = UNITS[2].read_text(encoding="utf-8")
        for concrete in ("User=hermes", "/home/hermes", "profile max"):
            with self.subTest(concrete=concrete):
                self.assertNotIn(concrete, text)


if __name__ == "__main__":
    unittest.main()
