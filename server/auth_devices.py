"""Paired-device tokens for the ingest API (contract/pairing-v1.md).

The coach bridge owns the pairing state and writes `devices.json` (token hashes only) into
`KORE_STATE_DIR`; Docker Compose mounts that directory read-only at /state. This module only
reads it, re-reading whenever the file was replaced (inode, mtime, size), so a revoke from the
bridge or `kore-doctor revoke` takes effect on the next request. No dependency on the bridge.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import threading

STATE_DIR_ENV = "KORE_STATE_DIR"
DEVICES_FILE = "devices.json"

log = logging.getLogger("kore.auth_devices")


class DeviceTokens:
    def __init__(self, state_dir: str) -> None:
        self.state_dir = state_dir
        self._path = os.path.join(state_dir, DEVICES_FILE)
        self._lock = threading.Lock()
        self._key: tuple | None = None
        self._entries: tuple[tuple[str, bytes], ...] = ()
        self._warned_key: object = None

    def _hashes(self) -> tuple[tuple[str, bytes], ...]:
        try:
            info = os.stat(self._path)
        except FileNotFoundError:
            return ()
        except OSError as error:
            self._warn_once(("stat", type(error).__name__), error)
            return ()
        key = (info.st_ino, info.st_mtime_ns, info.st_size)
        with self._lock:
            if key == self._key:
                return self._entries
        try:
            with open(self._path, encoding="utf-8") as handle:
                data = json.load(handle)
            entries = tuple(
                (device["id"], device["token_sha256"].encode("utf-8", "surrogatepass"))
                for device in data["devices"]
                if isinstance(device.get("id"), str) and isinstance(device.get("token_sha256"), str)
            )
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
            self._warn_once(key, error)
            return ()
        with self._lock:
            self._key, self._entries = key, entries
        return entries

    def _warn_once(self, key: object, error: Exception) -> None:
        if self._warned_key != key:
            self._warned_key = key
            log.warning(
                "cannot read %s in %s (%s); paired-device tokens are rejected",
                DEVICES_FILE, STATE_DIR_ENV, type(error).__name__,
            )

    def device_for_token(self, token: object) -> str | None:
        """Device id for a valid device token. Compares against every entry, no early exit."""
        if not isinstance(token, str) or not token:
            return None
        candidate = hashlib.sha256(token.encode("utf-8", "surrogatepass")).hexdigest().encode("ascii")
        found = None
        for device_id, expected in self._hashes():
            if hmac.compare_digest(candidate, expected) and found is None:
                found = device_id
        return found

    def device_for_authorization(self, authorization: object) -> str | None:
        if not isinstance(authorization, str) or not authorization.startswith("Bearer "):
            return None
        return self.device_for_token(authorization[len("Bearer "):])


def from_env(environ=None) -> DeviceTokens | None:
    """None when KORE_STATE_DIR is unset: then only the static ingest token is accepted."""
    source = os.environ if environ is None else environ
    state_dir = source.get(STATE_DIR_ENV, "").strip()
    return DeviceTokens(state_dir) if state_dir else None
