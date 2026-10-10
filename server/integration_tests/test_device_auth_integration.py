"""Paired devices against the real ingest app: pair via the bridge's pairing store, use, revoke."""

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
BRIDGE_ROOT = SERVER_ROOT.parent / "coach-bridge"
sys.path.insert(0, str(SERVER_ROOT))


def _load_pairing():
    # Only the test reaches into the bridge; server/auth_devices.py has no such dependency.
    sys.path.insert(0, str(BRIDGE_ROOT))
    try:
        return importlib.import_module("pairing")
    finally:
        sys.path.remove(str(BRIDGE_ROOT))


class DeviceAuthIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.state_dir = Path(cls.temp_dir.name) / "state"
        cls.static_token = secrets.token_urlsafe(32)
        cls._saved_env = {k: os.environ.get(k) for k in (
            "KORE_DB_PATH", "KORE_INSTANCE_ID", "KORE_INGEST_TOKEN",
            "KORE_PLAN_ORIGIN_PRINCIPAL", "KORE_STATE_DIR",
        )}
        os.environ.update({
            "KORE_DB_PATH": str(Path(cls.temp_dir.name) / "api.db"),
            "KORE_INSTANCE_ID": "device-auth",
            "KORE_INGEST_TOKEN": cls.static_token,
            "KORE_PLAN_ORIGIN_PRINCIPAL": "device-auth",
            "KORE_STATE_DIR": str(cls.state_dir),
        })
        for name in ("ingest_api", "store", "models", "origin_ticket", "auth_devices"):
            sys.modules.pop(name, None)
        cls.ingest_api = importlib.import_module("ingest_api")
        cls.pairing = _load_pairing()
        cls.client_context = TestClient(cls.ingest_api.app)
        cls.client = cls.client_context.__enter__()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client_context.__exit__(None, None, None)
        for key, value in cls._saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        cls.temp_dir.cleanup()

    def status(self, token: str) -> int:
        return self.client.get("/plan/pending", headers={"Authorization": f"Bearer {token}"}).status_code

    def test_device_token_works_until_revoked_and_static_token_stays(self) -> None:
        store = self.pairing.PairingStore(str(self.state_dir))
        code = store.create_code()["code"]
        device = store.redeem(code, "Test phone", "10.0.0.10")
        other = store.redeem(store.create_code()["code"], "Second", "10.0.0.10")

        self.assertEqual(self.status(device["token"]), 200)
        self.assertEqual(self.status(other["token"]), 200)
        self.assertEqual(self.status(self.static_token), 200)
        self.assertEqual(self.status("x" * 43), 401)
        self.assertEqual(self.client.get("/plan/pending").status_code, 401)

        self.assertTrue(store.revoke(device["device_id"]))
        self.assertEqual(self.status(device["token"]), 401)
        self.assertEqual(self.status(other["token"]), 200)
        self.assertEqual(self.status(self.static_token), 200)

    def test_one_time_code_is_not_a_token(self) -> None:
        store = self.pairing.PairingStore(str(self.state_dir))
        code = store.create_code()["code"]
        self.assertEqual(self.status(code), 401)


if __name__ == "__main__":
    unittest.main()
