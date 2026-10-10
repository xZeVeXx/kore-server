"""Device pairing (contract/pairing-v1.md): one-time code -> device token, stored only as a hash."""
import contextlib
import http.client
import io
import json
import logging
import os
import re
import stat
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import bridge
import pairing

T0 = 1_800_000_000.0
STATIC_TOKEN = "static-test-token-" + "a" * 24


class StoreCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.state_dir = Path(self._tmp.name) / "state"
        self.store = pairing.PairingStore(str(self.state_dir))

    def tearDown(self):
        self._tmp.cleanup()

    def new_code(self, now=T0):
        return self.store.create_code(now=now)["code"]

    def pair(self, name="iPhone", ip="10.0.0.10", now=T0):
        code = self.new_code(now=now)
        return code, self.store.redeem(code, name, ip, now=now)


class CodeTests(StoreCase):
    def test_code_is_20_char_base32_without_padding_and_file_holds_only_its_hash(self):
        created = self.store.create_code(now=T0)
        code = created["code"]
        self.assertRegex(code, r"\A[A-Z2-7]{20}\Z")
        self.assertEqual(created["expires_at"], int(T0) + 600)
        self.assertRegex(created["server_id"], r"\A[0-9a-f]{8}\Z")
        raw = (self.state_dir / "pairing.json").read_text(encoding="utf-8")
        self.assertNotIn(code, raw)
        self.assertEqual(json.loads(raw), {
            "code_sha256": pairing.hash_secret(code),
            "expires_at": int(T0) + 600,
            "attempts": 0,
        })

    def test_at_most_one_open_code(self):
        first = self.new_code()
        second = self.new_code()
        with self.assertRaises(pairing.PairingFailed):
            self.store.redeem(first, "iPhone", "10.0.0.10", now=T0)
        # The first attempt consumed the only open code, even though it was the wrong one.
        with self.assertRaises(pairing.PairingFailed):
            self.store.redeem(second, "iPhone", "192.0.2.11", now=T0)

    def test_success_returns_contract_payload(self):
        _, result = self.pair()
        self.assertEqual(set(result), {"token", "device_id", "server_id", "scopes"})
        self.assertRegex(result["token"], r"\A[A-Za-z0-9_-]{43}\Z")
        self.assertRegex(result["device_id"], r"\A[0-9a-f]{8}\Z")
        self.assertEqual(result["server_id"], self.store.server_id())
        self.assertEqual(result["scopes"], ["coach", "ingest"])

    def test_code_is_consumed_after_a_successful_attempt(self):
        code, _ = self.pair()
        with self.assertRaises(pairing.PairingFailed):
            self.store.redeem(code, "iPhone", "192.0.2.11", now=T0 + 1)
        self.assertEqual(len(self.store.list_devices()), 1)

    def test_code_is_consumed_after_a_failed_attempt(self):
        code = self.new_code()
        with self.assertRaises(pairing.PairingFailed):
            self.store.redeem("A" * 20, "iPhone", "10.0.0.10", now=T0)
        with self.assertRaises(pairing.PairingFailed):
            self.store.redeem(code, "iPhone", "192.0.2.11", now=T0 + 1)
        self.assertEqual(self.store.list_devices(), [])

    def test_code_expires_after_600_seconds(self):
        code = self.new_code(now=T0)
        with self.assertRaises(pairing.PairingFailed):
            self.store.redeem(code, "iPhone", "10.0.0.10", now=T0 + 600)
        code = self.new_code(now=T0)
        self.assertIn("token", self.store.redeem(code, "iPhone", "10.0.0.10", now=T0 + 599))

    def test_lowercase_code_with_whitespace_is_accepted(self):
        code = self.new_code()
        self.assertIn("token", self.store.redeem(f" {code.lower()} ", "iPhone", "10.0.0.10", now=T0))

    def test_no_open_code_fails(self):
        self.state_dir.mkdir(mode=0o700)
        with self.assertRaises(pairing.PairingFailed):
            self.store.redeem("A" * 20, "iPhone", "10.0.0.10", now=T0)

    def test_non_string_code_fails_and_consumes(self):
        code = self.new_code()
        with self.assertRaises(pairing.PairingFailed):
            self.store.redeem(None, "iPhone", "10.0.0.10", now=T0)
        with self.assertRaises(pairing.PairingFailed):
            self.store.redeem(code, "iPhone", "192.0.2.11", now=T0)


class RateLimitTests(StoreCase):
    def test_sixth_attempt_within_a_minute_is_rate_limited_and_keeps_the_code(self):
        self.state_dir.mkdir(mode=0o700)
        for i in range(5):
            with self.assertRaises(pairing.PairingFailed):
                self.store.redeem("A" * 20, "x", "192.0.2.7", now=T0 + i)
        code = self.new_code(now=T0 + 5)
        with self.assertRaises(pairing.RateLimited):
            self.store.redeem(code, "x", "192.0.2.7", now=T0 + 6)
        # Another address is not affected, and the limited attempt did not burn the code.
        self.assertIn("token", self.store.redeem(code, "x", "192.0.2.8", now=T0 + 7))

    def test_limit_resets_after_a_minute(self):
        self.state_dir.mkdir(mode=0o700)
        for i in range(5):
            with self.assertRaises(pairing.PairingFailed):
                self.store.redeem("A" * 20, "x", "192.0.2.7", now=T0 + i)
        code = self.new_code(now=T0 + 61)
        self.assertIn("token", self.store.redeem(code, "x", "192.0.2.7", now=T0 + 65))

    def test_success_does_not_count_as_failure(self):
        for i in range(6):
            code = self.new_code(now=T0 + i)
            self.store.redeem(code, "x", "192.0.2.7", now=T0 + i)
        self.assertEqual(len(self.store.list_devices()), 6)


class TokenStorageTests(StoreCase):
    def test_token_is_stored_only_as_its_hash(self):
        code, result = self.pair(name="Test phone")
        token = result["token"]
        for name in ("devices.json", "pairing.json", "server_id"):
            raw = (self.state_dir / name).read_text(encoding="utf-8")
            self.assertNotIn(token, raw)
            self.assertNotIn(code, raw)
        devices = json.loads((self.state_dir / "devices.json").read_text(encoding="utf-8"))
        self.assertEqual(devices, {"devices": [{
            "id": result["device_id"],
            "name": "Test phone",
            "token_sha256": pairing.hash_secret(token),
            "created_at": int(T0),
            "last_seen": None,
        }]})

    def test_verify_token(self):
        _, first = self.pair(now=T0)
        _, second = self.pair(now=T0 + 1)
        self.assertEqual(self.store.verify_token(first["token"]), first["device_id"])
        self.assertEqual(self.store.verify_token(second["token"]), second["device_id"])
        for bad in ("", "x" * 43, first["token"][:-1], None, "é" * 43):
            with self.subTest(bad=ascii(bad)):
                self.assertIsNone(self.store.verify_token(bad))

    def test_verify_compares_every_entry_without_early_exit(self):
        _, first = self.pair(now=T0)
        self.pair(now=T0 + 1)
        self.pair(now=T0 + 2)
        calls = []
        real = pairing.hmac.compare_digest

        def counting(a, b):
            calls.append(1)
            return real(a, b)

        with patch.object(pairing.hmac, "compare_digest", counting):
            self.assertEqual(self.store.verify_token(first["token"]), first["device_id"])
        self.assertEqual(len(calls), 3)

    def test_revoke_blocks_immediately_and_list_hides_hashes(self):
        _, kept = self.pair(now=T0)
        _, gone = self.pair(now=T0 + 1)
        self.assertTrue(self.store.revoke(gone["device_id"]))
        self.assertFalse(self.store.revoke(gone["device_id"]))
        self.assertIsNone(self.store.verify_token(gone["token"]))
        self.assertEqual(self.store.verify_token(kept["token"]), kept["device_id"])
        listed = self.store.list_devices()
        self.assertEqual([d["id"] for d in listed], [kept["device_id"]])
        self.assertEqual(set(listed[0]), {"id", "name", "created_at", "last_seen"})

    def test_revoke_by_another_store_instance_is_seen_at_once(self):
        _, result = self.pair()
        self.assertEqual(self.store.verify_token(result["token"]), result["device_id"])
        other = pairing.PairingStore(str(self.state_dir))
        other.revoke(result["device_id"])
        self.assertIsNone(self.store.verify_token(result["token"]))

    def test_last_seen_is_throttled_to_once_a_minute(self):
        _, result = self.pair()
        device_id = result["device_id"]

        def last_seen():
            return next(d["last_seen"] for d in self.store.list_devices() if d["id"] == device_id)

        self.store.touch(device_id, now=T0 + 10)
        self.assertEqual(last_seen(), int(T0) + 10)
        self.store.touch(device_id, now=T0 + 30)
        self.assertEqual(last_seen(), int(T0) + 10)
        self.store.touch(device_id, now=T0 + 71)
        self.assertEqual(last_seen(), int(T0) + 71)

    def test_touch_does_not_resurrect_a_revoked_device(self):
        _, result = self.pair()
        self.store.revoke(result["device_id"])
        self.store.touch(result["device_id"], now=T0 + 100)
        self.assertEqual(self.store.list_devices(), [])

    def test_server_id_is_created_once(self):
        self.state_dir.mkdir(mode=0o700)
        first = self.store.server_id()
        self.assertRegex(first, r"\A[0-9a-f]{8}\Z")
        self.assertEqual(pairing.PairingStore(str(self.state_dir)).server_id(), first)
        self.assertEqual((self.state_dir / "server_id").read_text(encoding="utf-8").strip(), first)


class DeviceNameTests(unittest.TestCase):
    def test_sanitized(self):
        cases = {
            "iPhone von A": "iPhone von A",
            "  spaced  ": "spaced",
            "bad\x00name\n\t\x1b[31m": "badname[31m",
            "x" * 60: "x" * 40,
            "": "Device",
            "\n\n": "Device",
            None: "Device",
            42: "Device",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=ascii(raw)):
                self.assertEqual(pairing.sanitize_device_name(raw), expected)


class OperatorLabelTests(StoreCase):
    def test_operator_name_wins_over_the_apps_and_is_cleaned(self):
        code = self.store.create_code(now=T0, device_name="  Wohnzimmer\x07 iPad ")["code"]
        self.assertEqual(json.loads((self.state_dir / "pairing.json").read_text())["device_name"],
                         "Wohnzimmer iPad")
        result = self.store.redeem(code, "iPhone", "10.0.0.10", now=T0)
        self.assertEqual(self.store.list_devices()[0]["name"], "Wohnzimmer iPad")
        self.assertEqual(self.store.list_devices()[0]["id"], result["device_id"])

    def test_blank_operator_name_changes_nothing(self):
        code = self.store.create_code(now=T0, device_name="   ")["code"]
        self.assertNotIn("device_name", json.loads((self.state_dir / "pairing.json").read_text()))
        self.store.redeem(code, "iPhone", "10.0.0.10", now=T0)
        self.assertEqual(self.store.list_devices()[0]["name"], "iPhone")


class PermissionTests(StoreCase):
    def mode(self, path):
        return stat.S_IMODE(os.stat(path).st_mode)

    def group_can_read(self, path):
        """Mode-bit simulation of an ingest process that is only in the state group."""
        path = Path(path)
        dir_ok = self.mode(path.parent) & stat.S_IXGRP
        return bool(dir_ok and self.mode(path) & stat.S_IRGRP)

    def other_can_access(self, path):
        path = Path(path)
        return bool(self.mode(path.parent) & stat.S_IXOTH or self.mode(path) & 0o007)

    def assert_layout(self):
        self.assertEqual(self.mode(self.state_dir), 0o750)
        self.assertEqual(self.mode(self.state_dir / "devices.json"), 0o640)
        for name in ("pairing.json", "server_id", ".lock"):
            with self.subTest(name=name):
                self.assertEqual(self.mode(self.state_dir / name), 0o600)
        self.assertEqual(self.mode(self.state_dir / "devices.json") & 0o007, 0)
        self.assertFalse(self.other_can_access(self.state_dir / "devices.json"))
        self.assertTrue(self.group_can_read(self.state_dir / "devices.json"))
        for name in ("pairing.json", "server_id"):
            self.assertFalse(self.group_can_read(self.state_dir / name))
        leftovers = [p.name for p in self.state_dir.iterdir() if p.name.endswith(".tmp")]
        self.assertEqual(leftovers, [])

    def test_dir_0750_devices_0640_others_0600(self):
        old = os.umask(0o022)
        try:
            self.pair()
        finally:
            os.umask(old)
        self.assert_layout()

    def test_modes_do_not_depend_on_the_umask(self):
        for umask in (0o000, 0o077):
            with self.subTest(umask=oct(umask)):
                self._tmp.cleanup()
                self.setUp()
                old = os.umask(umask)
                try:
                    self.pair()
                finally:
                    os.umask(old)
                self.assert_layout()

    def test_modes_survive_every_later_write(self):
        code, result = self.pair()
        # last_seen update, a second code, a second device, then revoke: all atomic rewrites.
        self.store.touch(result["device_id"], now=T0 + 120)
        self.assert_layout()
        self.pair(name="iPad", now=T0 + 130)
        self.assert_layout()
        self.assertTrue(self.store.revoke(result["device_id"]))
        self.assert_layout()
        self.assertEqual(len(self.store.list_devices()), 1)

    def test_failed_redeem_keeps_modes(self):
        self.new_code()
        with self.assertRaises(pairing.PairingFailed):
            self.store.redeem("WRONG", "iPhone", "10.0.0.10", now=T0)
        self.assertEqual(self.mode(self.state_dir / "pairing.json"), 0o600)

    def test_new_state_dir_group_is_the_creating_processes_group(self):
        self.new_code()
        info = os.stat(self.state_dir)
        self.assertEqual(info.st_uid, os.geteuid())
        # No chown anywhere: the group is whatever the creating process (the bridge user) gets.
        self.assertIn(info.st_gid, [os.getegid(), os.stat(self._tmp.name).st_gid])

    def test_unavailable_when_dir_missing_or_not_writable(self):
        self.assertIsNotNone(self.store.unavailable_reason())
        self.state_dir.mkdir(mode=0o700)
        self.assertIsNone(self.store.unavailable_reason())
        if os.geteuid() != 0:
            os.chmod(self.state_dir, 0o500)
            try:
                self.assertIsNotNone(self.store.unavailable_reason())
            finally:
                os.chmod(self.state_dir, 0o700)

    def test_redeem_never_creates_the_dir(self):
        with self.assertRaises(pairing.PairingFailed):
            self.store.redeem("A" * 20, "x", "10.0.0.10", now=T0)
        self.assertFalse(self.state_dir.exists())


class ConcurrencyTests(StoreCase):
    def test_two_threads_redeem_the_same_code_exactly_one_wins(self):
        for round_ in range(20):
            code = self.new_code(now=T0 + round_)
            barrier = threading.Barrier(2)
            results = []

            def attempt(ip):
                barrier.wait()
                try:
                    results.append(self.store.redeem(code, "x", ip, now=T0 + round_))
                except pairing.PairingFailed:
                    results.append(None)

            threads = [threading.Thread(target=attempt, args=(f"192.0.2.{n}",)) for n in (20, 21)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertEqual(len(results), 2, f"round {round_}")
            self.assertEqual(sum(r is not None for r in results), 1, f"round {round_}")
            self.store._failures.clear()
        self.assertEqual(len(self.store.list_devices()), 20)


class LoggingTests(StoreCase):
    def test_logs_never_contain_code_or_token(self):
        with self.assertLogs("kore.pairing", level="DEBUG") as captured:
            code, result = self.pair()
            with self.assertRaises(pairing.PairingFailed):
                self.store.redeem(code, "x", "10.0.0.10", now=T0)
            self.store.verify_token(result["token"])
            self.store.revoke(result["device_id"])
        text = "\n".join(captured.output)
        self.assertTrue(text)
        self.assertNotIn(code, text)
        self.assertNotIn(code.lower(), text)
        self.assertNotIn(result["token"], text)
        self.assertNotIn(pairing.hash_secret(result["token"]), text)


# --- HTTP through the bridge -------------------------------------------------------------


class BridgeHTTPCase(StoreCase):
    def setUp(self):
        super().setUp()
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.object(bridge, "TOKEN", STATIC_TOKEN))
        stack.enter_context(patch.object(bridge, "PAIRING", self.store))
        self.server = bridge.ThreadingHTTPServer(("127.0.0.1", 0), bridge.Handler)
        thread = threading.Thread(target=self.server.serve_forever, args=(0.01,), daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def request(self, method, path, body=None, headers=None, raw=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=5)
        try:
            payload = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
            connection.request(method, path, body=payload, headers=headers or {})
            response = connection.getresponse()
            data = response.read()
            return response.status, (json.loads(data) if data else None)
        finally:
            connection.close()

    def http_pair(self, code, name="iPhone"):
        return self.request("POST", "/pair", {"code": code, "device_name": name},
                            {"Content-Type": "application/json"})


class BridgePairRouteTests(BridgeHTTPCase):
    def test_pair_success_and_uniform_failure(self):
        code = self.new_code()
        status, body = self.http_pair(code)
        self.assertEqual(status, 200)
        self.assertEqual(set(body), {"token", "device_id", "server_id", "scopes"})
        self.assertEqual(body["scopes"], ["coach", "ingest"])
        self.assertEqual(self.http_pair(code), (401, {"error": "pairing_failed"}))

    def test_malformed_bodies_are_uniform_401(self):
        for raw in (b"not json", b"[]", b'{"code": 5}', b"{}"):
            with self.subTest(raw=raw):
                self.new_code()
                status, body = self.request("POST", "/pair", raw=raw)
                self.assertEqual((status, body), (401, {"error": "pairing_failed"}))
                self.store._failures.clear()

    def test_rate_limited_after_five_failures(self):
        self.state_dir.mkdir(mode=0o700)
        for _ in range(5):
            self.assertEqual(self.http_pair("A" * 20)[0], 401)
        self.assertEqual(self.http_pair("A" * 20), (429, {"error": "rate_limited"}))

    def test_pair_needs_no_bearer_and_ignores_a_wrong_one(self):
        code = self.new_code()
        status, _ = self.request("POST", "/pair", {"code": code, "device_name": "x"},
                                 {"Authorization": "Bearer wrong"})
        self.assertEqual(status, 200)

    def test_browser_origin_is_rejected_before_the_code_is_touched(self):
        code = self.new_code()
        status, _ = self.request("POST", "/pair", {"code": code, "device_name": "x"},
                                 {"Origin": "http://192.0.2.1"})
        self.assertEqual(status, 403)
        self.assertEqual(self.http_pair(code)[0], 200)

    def test_body_limit(self):
        self.new_code()
        status, _ = self.request("POST", "/pair", raw=b"{" + b" " * 8192 + b"}")
        self.assertEqual(status, 413)

    def test_unavailable_state_dir_is_uniform_401(self):
        self.assertEqual(self.http_pair("A" * 20), (401, {"error": "pairing_failed"}))

    def test_device_token_authorizes_existing_routes_and_static_still_works(self):
        _, result = self.pair()
        for token in (result["token"], STATIC_TOKEN):
            status, body = self.request("GET", "/capabilities", headers={"Authorization": f"Bearer {token}"})
            self.assertEqual(status, 200)
            self.assertIn("device_pairing_v1", body["capabilities"])
        self.assertEqual(self.request("GET", "/health", headers={"Authorization": "Bearer nope"})[0], 401)

    def test_device_token_use_updates_last_seen(self):
        _, result = self.pair()
        self.request("GET", "/capabilities", headers={"Authorization": f"Bearer {result['token']}"})
        self.assertIsNotNone(self.store.list_devices()[0]["last_seen"])

    def test_unpair_removes_the_device_and_blocks_at_once(self):
        _, result = self.pair()
        auth = {"Authorization": f"Bearer {result['token']}"}
        self.assertEqual(self.request("POST", "/unpair", headers=auth), (204, None))
        self.assertEqual(self.store.list_devices(), [])
        self.assertEqual(self.request("GET", "/health", headers=auth)[0], 401)
        self.assertEqual(self.request("POST", "/unpair", headers=auth)[0], 401)

    def test_unpair_requires_a_device_token(self):
        self.pair()
        self.assertEqual(self.request("POST", "/unpair")[0], 401)
        static = {"Authorization": f"Bearer {STATIC_TOKEN}"}
        self.assertEqual(self.request("POST", "/unpair", headers=static)[0], 401)
        self.assertEqual(len(self.store.list_devices()), 1)

    def test_cli_revoke_blocks_the_bridge_at_once(self):
        _, result = self.pair()
        auth = {"Authorization": f"Bearer {result['token']}"}
        self.assertEqual(self.request("GET", "/health", headers=auth)[0], 200)
        pairing.PairingStore(str(self.state_dir)).revoke(result["device_id"])
        self.assertEqual(self.request("GET", "/health", headers=auth)[0], 401)

    def test_no_code_or_token_reaches_logs_or_stderr(self):
        stderr = io.StringIO()
        handler = logging.StreamHandler(stderr)
        logger = logging.getLogger("kore.pairing")
        logger.addHandler(handler)
        old_level = logger.level
        logger.setLevel(logging.DEBUG)
        try:
            with patch("sys.stderr", stderr):
                code = self.new_code()
                _, result = self.http_pair(code)
                self.http_pair(code)
                auth = {"Authorization": f"Bearer {result['token']}"}
                self.request("GET", "/health", headers=auth)
                self.request("POST", "/unpair", headers=auth)
        finally:
            logger.removeHandler(handler)
            logger.setLevel(old_level)
        text = stderr.getvalue()
        self.assertIn("paired", text)
        self.assertNotIn(code, text)
        self.assertNotIn(result["token"], text)


class CapabilityTests(StoreCase):
    def test_capability_only_when_pairing_is_usable(self):
        with patch.object(bridge, "PAIRING", self.store):
            self.assertNotIn("device_pairing_v1", bridge.capabilities_payload()["capabilities"])
            self.state_dir.mkdir(mode=0o700)
            self.assertIn("device_pairing_v1", bridge.capabilities_payload()["capabilities"])

    def test_startup_message_names_the_dir_problem_without_secrets(self):
        with patch.object(bridge, "PAIRING", self.store):
            message = bridge.pairing_startup_warning()
            self.assertIn("KORE_STATE_DIR", message)
            self.state_dir.mkdir(mode=0o700)
            self.assertIsNone(bridge.pairing_startup_warning())

    def test_default_state_dir_is_next_to_the_compose_file(self):
        repo = Path(pairing.__file__).resolve().parent.parent
        self.assertTrue((repo / "docker-compose.yml").exists())
        self.assertEqual(pairing.state_dir_from_env({}), str(repo / "state"))
        self.assertEqual(pairing.state_dir_from_env({"KORE_STATE_DIR": "/srv/kore/state"}), "/srv/kore/state")


if __name__ == "__main__":
    unittest.main()
