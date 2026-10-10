"""Device pairing for the Kore coach bridge (contract/pairing-v1.md). Standard library only.

A one-time code (20 Base32 characters, valid 600 s, consumed by the first redeem attempt) is
exchanged for a device token (256 bit). The token leaves the server exactly once, in the
`POST /pair` response; on disk there is only its SHA-256.

State lives in `KORE_STATE_DIR` (default: `state/` in the repository root, next to
`docker-compose.yml`, which mounts the same directory read-only into the ingest container):

    pairing.json  {"code_sha256": "<hex>", "expires_at": <unix>, "attempts": <int>}  (one open code)
    devices.json  {"devices": [{"id", "name", "token_sha256", "created_at", "last_seen"}]}
    server_id     8 hex characters, created once

Directory 0700, files 0600, every write atomic (temp file + os.replace). Writers (bridge, CLI)
serialise on an flock'ed `.lock` file in the same directory, so a revoke from the CLI cannot be
undone by a concurrent last_seen update in the bridge.

For the CLI (`kore-doctor pair/devices/revoke`):

    store = PairingStore(state_dir_from_env())
    created = store.create_code()      # {"code", "expires_at", "server_id"}; creates the dir if needed
    store.list_devices()               # without token hashes
    store.revoke(device_id)            # True if a device was removed

Logs (logger `kore.pairing`) never contain a code, a token or a hash of either.
"""
import fcntl
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import threading
import time
from contextlib import contextmanager

STATE_DIR_ENV = "KORE_STATE_DIR"
PAIRING_FILE = "pairing.json"
DEVICES_FILE = "devices.json"
SERVER_ID_FILE = "server_id"
LOCK_FILE = ".lock"

CODE_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"  # RFC 4648 Base32, no padding
CODE_LENGTH = 20  # 100 bit
CODE_TTL_S = 600
MAX_FAILURES_PER_WINDOW = 5
RATE_WINDOW_S = 60
DEVICE_NAME_MAX = 40
DEFAULT_DEVICE_NAME = "Device"
LAST_SEEN_INTERVAL_S = 60
SCOPES = ("coach", "ingest")

_ID_RE = re.compile(r"[0-9a-f]{8}\Z")

log = logging.getLogger("kore.pairing")


class PairingFailed(Exception):
    """Any failed redeem. Deliberately carries no reason: the caller answers uniformly."""


class RateLimited(Exception):
    """Too many failed attempts from one client address within RATE_WINDOW_S."""


def default_state_dir() -> str:
    """`state/` in the repository root: the directory docker-compose.yml mounts as ./state."""
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(here, os.pardir, "state"))


def state_dir_from_env(environ=None) -> str:
    source = os.environ if environ is None else environ
    value = source.get(STATE_DIR_ENV, "").strip()
    return os.path.abspath(value) if value else default_state_dir()


def hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", "surrogatepass")).hexdigest()


def sanitize_device_name(value) -> str:
    if not isinstance(value, str):
        return DEFAULT_DEVICE_NAME
    cleaned = "".join(ch for ch in value if ch.isprintable()).strip()[:DEVICE_NAME_MAX].strip()
    return cleaned or DEFAULT_DEVICE_NAME


def _new_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


def _normalized_code(value) -> str:
    return value.strip().upper() if isinstance(value, str) else ""


class PairingStore:
    def __init__(self, state_dir: str):
        self.state_dir = state_dir
        self._write_lock = threading.Lock()
        self._memory_lock = threading.Lock()
        self._failures = {}  # client address -> [failure times]
        self._touched = {}  # device id -> last written last_seen
        self._cache_key = None
        self._cache_entries = ()
        self._cache_warned = None

    # --- files -----------------------------------------------------------------------------

    def _path(self, name: str) -> str:
        return os.path.join(self.state_dir, name)

    def unavailable_reason(self):
        """None when pairing can work; otherwise a non-secret sentence for the operator."""
        if not os.path.isdir(self.state_dir):
            return f"{STATE_DIR_ENV} {self.state_dir} does not exist (kore-doctor pair creates it)"
        if not os.access(self.state_dir, os.W_OK | os.X_OK):
            return f"{STATE_DIR_ENV} {self.state_dir} is not writable for this user"
        return None

    def _ensure_dir(self) -> None:
        if not os.path.isdir(self.state_dir):
            os.mkdir(self.state_dir, 0o700)
            os.chmod(self.state_dir, 0o700)

    @contextmanager
    def _locked(self):
        with self._write_lock:
            fd = os.open(self._path(LOCK_FILE), os.O_RDWR | os.O_CREAT, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
                yield
            finally:
                os.close(fd)  # releases the flock

    def _read_json(self, name: str, default):
        try:
            with open(self._path(name), encoding="utf-8") as handle:
                return json.load(handle)
        except FileNotFoundError:
            return default

    def _write_text(self, name: str, text: str) -> None:
        tmp = self._path(f".{name}.{secrets.token_hex(4)}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(tmp, 0o600)
            os.replace(tmp, self._path(name))
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def _write_json(self, name: str, obj) -> None:
        self._write_text(name, json.dumps(obj, indent=2, ensure_ascii=False) + "\n")

    def _devices_locked(self) -> list:
        data = self._read_json(DEVICES_FILE, {"devices": []})
        devices = data.get("devices") if isinstance(data, dict) else None
        if not isinstance(devices, list):
            raise ValueError(f"{DEVICES_FILE} is malformed")
        return devices

    def _server_id_locked(self) -> str:
        try:
            with open(self._path(SERVER_ID_FILE), encoding="utf-8") as handle:
                value = handle.read().strip()
        except FileNotFoundError:
            value = secrets.token_hex(4)
            self._write_text(SERVER_ID_FILE, value)
            log.info("pairing: created server id %s", value)
        if _ID_RE.fullmatch(value) is None:
            raise ValueError(f"{SERVER_ID_FILE} is malformed")
        return value

    # --- public API ------------------------------------------------------------------------

    def server_id(self) -> str:
        with self._locked():
            return self._server_id_locked()

    def create_code(self, now=None) -> dict:
        """Replace any open code with a fresh one. The plain code is only in the return value."""
        now = time.time() if now is None else now
        self._ensure_dir()
        code = _new_code()
        expires_at = int(now) + CODE_TTL_S
        with self._locked():
            sid = self._server_id_locked()
            self._write_json(PAIRING_FILE, {
                "code_sha256": hash_secret(code),
                "expires_at": expires_at,
                "attempts": 0,
            })
        log.info("pairing: new one-time code, valid until %s", expires_at)
        return {"code": code, "expires_at": expires_at, "server_id": sid}

    def redeem(self, code, device_name, client_ip, now=None) -> dict:
        now = time.time() if now is None else now
        with self._memory_lock:
            for address in list(self._failures):
                recent = [t for t in self._failures[address] if now - t < RATE_WINDOW_S]
                if recent:
                    self._failures[address] = recent
                else:
                    del self._failures[address]
            if len(self._failures.get(client_ip, ())) >= MAX_FAILURES_PER_WINDOW:
                log.warning("pairing: rate limited %s", client_ip)
                raise RateLimited()
        try:
            result = self._redeem(code, sanitize_device_name(device_name), now)
        except PairingFailed:
            with self._memory_lock:
                self._failures.setdefault(client_ip, []).append(now)
            log.warning("pairing: failed attempt from %s", client_ip)
            raise
        log.info("pairing: paired device %s from %s", result["device_id"], client_ip)
        return result

    def _redeem(self, code, name: str, now) -> dict:
        if self.unavailable_reason() is not None:
            raise PairingFailed()
        supplied = hash_secret(_normalized_code(code)).encode("ascii")
        with self._locked():
            pending = self._read_json(PAIRING_FILE, None)
            if not isinstance(pending, dict):
                raise PairingFailed()
            attempts = pending.get("attempts")
            expires_at = pending.get("expires_at")
            expected = pending.get("code_sha256")
            if not (isinstance(attempts, int) and isinstance(expires_at, (int, float))
                    and isinstance(expected, str)):
                raise PairingFailed()
            # Consume first: the code is gone after this attempt, whatever its outcome.
            self._write_json(PAIRING_FILE, {**pending, "attempts": attempts + 1})
            matches = hmac.compare_digest(supplied, expected.encode("utf-8", "surrogatepass"))
            if attempts != 0 or now >= expires_at or not matches:
                raise PairingFailed()
            devices = self._devices_locked()
            taken = {d.get("id") for d in devices if isinstance(d, dict)}
            device_id = secrets.token_hex(4)
            while device_id in taken:
                device_id = secrets.token_hex(4)
            token = secrets.token_urlsafe(32)
            devices.append({
                "id": device_id,
                "name": name,
                "token_sha256": hash_secret(token),
                "created_at": int(now),
                "last_seen": None,
            })
            self._write_json(DEVICES_FILE, {"devices": devices})
            sid = self._server_id_locked()
        return {"token": token, "device_id": device_id, "server_id": sid, "scopes": list(SCOPES)}

    def _device_hashes(self) -> tuple:
        """(id, hash bytes) pairs, re-read whenever devices.json was replaced."""
        path = self._path(DEVICES_FILE)
        try:
            info = os.stat(path)
        except FileNotFoundError:
            return ()
        except OSError:
            return ()
        key = (info.st_ino, info.st_mtime_ns, info.st_size)
        with self._memory_lock:
            if key == self._cache_key:
                return self._cache_entries
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
            entries = tuple(
                (d["id"], d["token_sha256"].encode("utf-8", "surrogatepass"))
                for d in data["devices"]
                if isinstance(d.get("id"), str) and isinstance(d.get("token_sha256"), str)
            )
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
            if self._cache_warned != key:
                self._cache_warned = key
                log.warning("pairing: cannot read %s (%s); device tokens rejected",
                            DEVICES_FILE, type(error).__name__)
            return ()
        with self._memory_lock:
            self._cache_key, self._cache_entries = key, entries
        return entries

    def verify_token(self, token):
        """Device id for a valid device token, else None. Compares against every entry."""
        if not isinstance(token, str) or not token:
            return None
        candidate = hash_secret(token).encode("ascii")
        found = None
        for device_id, expected in self._device_hashes():
            if hmac.compare_digest(candidate, expected) and found is None:
                found = device_id
        return found

    def touch(self, device_id: str, now=None) -> None:
        """Record last_seen, at most once per LAST_SEEN_INTERVAL_S per device. Never raises."""
        now = time.time() if now is None else now
        with self._memory_lock:
            last = self._touched.get(device_id)
            if last is not None and now - last < LAST_SEEN_INTERVAL_S:
                return
            self._touched[device_id] = now
        try:
            with self._locked():
                devices = self._devices_locked()
                for device in devices:
                    if isinstance(device, dict) and device.get("id") == device_id:
                        device["last_seen"] = int(now)
                        self._write_json(DEVICES_FILE, {"devices": devices})
                        break
        except (OSError, ValueError) as error:
            log.warning("pairing: could not record last_seen (%s)", type(error).__name__)

    def revoke(self, device_id: str) -> bool:
        if not os.path.isdir(self.state_dir):
            return False
        with self._locked():
            devices = self._devices_locked()
            kept = [d for d in devices if not (isinstance(d, dict) and d.get("id") == device_id)]
            if len(kept) == len(devices):
                return False
            self._write_json(DEVICES_FILE, {"devices": kept})
        with self._memory_lock:
            self._touched.pop(device_id, None)
        log.info("pairing: revoked device %s", device_id)
        return True

    def list_devices(self) -> list:
        """Paired devices without token hashes, oldest first."""
        if not os.path.isdir(self.state_dir):
            return []
        with self._locked():
            devices = self._devices_locked()
        return [
            {key: d.get(key) for key in ("id", "name", "created_at", "last_seen")}
            for d in devices if isinstance(d, dict)
        ]
