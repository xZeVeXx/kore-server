"""Device tokens on the ingest side: read-only from the shared pairing state (contract/pairing-v1.md)."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

SERVER_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_ROOT))

import auth_devices  # noqa: E402

TOKEN_A = "A" * 43
TOKEN_B = "b" * 42 + "-"


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class DeviceTokenTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.state_dir = Path(self._tmp.name)
        self.tokens = auth_devices.DeviceTokens(str(self.state_dir))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def write(self, *pairs: tuple[str, str]) -> None:
        # Atomic replace like the bridge does, so the cache key changes even within one tick.
        tmp = self.state_dir / ".devices.tmp"
        tmp.write_text(json.dumps({"devices": [
            {"id": device_id, "name": "x", "token_sha256": _sha(token),
             "created_at": 1, "last_seen": None}
            for device_id, token in pairs
        ]}), encoding="utf-8")
        os.replace(tmp, self.state_dir / "devices.json")

    def test_missing_file_means_no_devices(self) -> None:
        self.assertIsNone(self.tokens.device_for_token(TOKEN_A))

    def test_group_readable_layout_is_read(self) -> None:
        # The bridge user owns the state; the ingest process only has the group (0750 dir, 0640 file).
        os.chmod(self.state_dir, 0o750)
        self.write(("0000000a", TOKEN_A))
        os.chmod(self.state_dir / "devices.json", 0o640)
        self.assertEqual(self.tokens.device_for_token(TOKEN_A), "0000000a")
        self.assertEqual(os.stat(self.state_dir / "devices.json").st_mode & 0o007, 0)

    def test_known_tokens_map_to_their_device(self) -> None:
        self.write(("0000000a", TOKEN_A), ("0000000b", TOKEN_B))
        self.assertEqual(self.tokens.device_for_token(TOKEN_A), "0000000a")
        self.assertEqual(self.tokens.device_for_token(TOKEN_B), "0000000b")
        for bad in ("", TOKEN_A[:-1], "Z" * 43, "é" * 43, None):
            with self.subTest(bad=ascii(bad)):
                self.assertIsNone(self.tokens.device_for_token(bad))

    def test_removal_is_seen_on_the_next_call(self) -> None:
        self.write(("0000000a", TOKEN_A), ("0000000b", TOKEN_B))
        self.assertEqual(self.tokens.device_for_token(TOKEN_B), "0000000b")
        self.write(("0000000a", TOKEN_A))
        self.assertIsNone(self.tokens.device_for_token(TOKEN_B))
        self.assertEqual(self.tokens.device_for_token(TOKEN_A), "0000000a")

    def test_file_is_read_only_when_it_changed(self) -> None:
        self.write(("0000000a", TOKEN_A))
        reads = []
        real_open = open

        def counting_open(*args, **kwargs):
            reads.append(args[0])
            return real_open(*args, **kwargs)

        auth_devices.open = counting_open  # module-level name lookup
        try:
            for _ in range(3):
                self.tokens.device_for_token(TOKEN_A)
        finally:
            del auth_devices.open
        self.assertEqual(len(reads), 1)

    def test_every_entry_is_compared(self) -> None:
        self.write(("0000000a", TOKEN_A), ("0000000b", TOKEN_B), ("0000000c", "c" * 43))
        calls = []
        real = auth_devices.hmac.compare_digest

        def counting(a, b):
            calls.append(1)
            return real(a, b)

        auth_devices.hmac.compare_digest = counting
        try:
            self.assertEqual(self.tokens.device_for_token(TOKEN_A), "0000000a")
        finally:
            auth_devices.hmac.compare_digest = real
        self.assertEqual(len(calls), 3)

    def test_unreadable_or_malformed_file_rejects_device_tokens_and_warns_once(self) -> None:
        (self.state_dir / "devices.json").write_text("{not json", encoding="utf-8")
        with self.assertLogs("kore.auth_devices", level="WARNING") as captured:
            self.assertIsNone(self.tokens.device_for_token(TOKEN_A))
            self.assertIsNone(self.tokens.device_for_token(TOKEN_A))
        self.assertEqual(len(captured.output), 1)
        self.assertNotIn(TOKEN_A, captured.output[0])

    def test_never_writes(self) -> None:
        self.write(("0000000a", TOKEN_A))
        before = sorted(p.name for p in self.state_dir.iterdir())
        self.tokens.device_for_token(TOKEN_A)
        self.tokens.device_for_token("wrong")
        self.assertEqual(sorted(p.name for p in self.state_dir.iterdir()), before)

    def test_bearer_header_parsing(self) -> None:
        self.write(("0000000a", TOKEN_A))
        self.assertEqual(self.tokens.device_for_authorization(f"Bearer {TOKEN_A}"), "0000000a")
        for header in ("", TOKEN_A, f"bearer {TOKEN_A}", f"Basic {TOKEN_A}", "Bearer "):
            with self.subTest(header=header):
                self.assertIsNone(self.tokens.device_for_authorization(header))

    def test_from_env(self) -> None:
        self.assertIsNone(auth_devices.from_env({}))
        self.assertIsNone(auth_devices.from_env({"KORE_STATE_DIR": " "}))
        tokens = auth_devices.from_env({"KORE_STATE_DIR": str(self.state_dir)})
        self.assertEqual(tokens.state_dir, str(self.state_dir))


if __name__ == "__main__":
    unittest.main()
