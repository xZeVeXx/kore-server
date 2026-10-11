"""Ingest hardening against the real app (0.13 S11b): auth before body, no docs, route coverage."""

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

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient


SERVER_ROOT = Path(__file__).resolve().parents[1]
BRIDGE_ROOT = SERVER_ROOT.parent / "coach-bridge"
sys.path.insert(0, str(SERVER_ROOT))

_FRESH_MODULES = ("ingest_api", "store", "models", "origin_ticket", "auth_devices", "auth_gate")
_ENV_KEYS = (
    "KORE_DB_PATH", "KORE_INSTANCE_ID", "KORE_INGEST_TOKEN",
    "KORE_PLAN_ORIGIN_PRINCIPAL", "KORE_STATE_DIR", "KORE_MCP_ALLOWED_ORIGINS",
)


def _load_pairing():
    sys.path.insert(0, str(BRIDGE_ROOT))
    try:
        return importlib.import_module("pairing")
    finally:
        sys.path.remove(str(BRIDGE_ROOT))


class _Upload:
    """A large request body offered chunk by chunk; records everything the app asks for."""

    def __init__(self, chunks: int = 256, chunk_size: int = 64 * 1024) -> None:
        self.remaining = chunks
        self.chunk_size = chunk_size
        self.calls = 0

    async def __call__(self):
        self.calls += 1
        if self.remaining <= 0:
            return {"type": "http.disconnect"}
        self.remaining -= 1
        return {
            "type": "http.request",
            "body": b" " * self.chunk_size,
            "more_body": self.remaining > 0,
        }


class IngestHardeningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.state_dir = Path(cls.temp_dir.name) / "state"
        cls.token = secrets.token_urlsafe(32)
        cls._saved_env = {key: os.environ.get(key) for key in _ENV_KEYS}
        os.environ.update({
            "KORE_DB_PATH": str(Path(cls.temp_dir.name) / "hardening.db"),
            "KORE_INSTANCE_ID": "hardening",
            "KORE_INGEST_TOKEN": cls.token,
            "KORE_PLAN_ORIGIN_PRINCIPAL": "hardening",
            "KORE_STATE_DIR": str(cls.state_dir),
        })
        os.environ.pop("KORE_MCP_ALLOWED_ORIGINS", None)
        for name in _FRESH_MODULES:
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
        for name in _FRESH_MODULES:
            sys.modules.pop(name, None)
        cls.temp_dir.cleanup()

    def asgi(self, method: str, path: str, headers=(), upload: _Upload | None = None):
        """One request straight into the real app, without a server in between."""
        upload = upload or _Upload()
        sent: list = []

        async def send(message):
            sent.append(message)

        scope = {
            "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
            "method": method, "path": path, "raw_path": path.encode(), "query_string": b"",
            "root_path": "", "scheme": "http", "server": ("127.0.0.1", 8081),
            "client": ("10.0.0.10", 50000), "headers": list(headers),
        }
        asyncio.run(self.ingest_api.app(scope, upload, send))
        status = next(m["status"] for m in sent if m["type"] == "http.response.start")
        return status, upload

    # -- 1. authentication happens before the body is read -----------------------------------

    def test_a_wrong_or_missing_token_never_makes_the_app_read_the_body(self) -> None:
        routes = (("POST", "/ingest"), ("GET", "/plan/pending"),
                  ("POST", "/plan/6f9619ff-8b86-d011-b42d-00c04fc964ff/ack"))
        for header in ([], [(b"authorization", b"Bearer " + b"x" * 43)]):
            for method, path in routes:
                with self.subTest(method=method, path=path, header=bool(header)):
                    headers = [(b"content-type", b"application/json"),
                               (b"content-length", str(16 * 1024 * 1024).encode()), *header]
                    status, upload = self.asgi(method, path, headers)
                    self.assertEqual(status, 401)
                    self.assertEqual(upload.calls, 0, "the body must stay unread")

    def test_the_http_level_401_keeps_its_shape(self) -> None:
        response = self.client.post("/ingest", content=b"{}")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"detail": "Unauthorized"})
        self.assertEqual(response.headers["www-authenticate"], "Bearer")

    def test_the_static_token_still_works(self) -> None:
        response = self.client.get("/plan/pending", headers={"Authorization": f"Bearer {self.token}"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"plans": []})

    def test_a_paired_device_token_still_works_until_revoked(self) -> None:
        store = self.pairing.PairingStore(str(self.state_dir))
        device = store.redeem(store.create_code()["code"], "Test phone", "10.0.0.10")
        headers = {"Authorization": f"Bearer {device['token']}"}
        self.assertEqual(self.client.get("/plan/pending", headers=headers).status_code, 200)
        self.assertTrue(store.revoke(device["device_id"]))
        self.assertEqual(self.client.get("/plan/pending", headers=headers).status_code, 401)

    def test_an_authenticated_oversize_body_is_still_cut_at_16_mib(self) -> None:
        headers = [(b"authorization", f"Bearer {self.token}".encode()),
                   (b"content-length", str(16 * 1024 * 1024 + 1).encode())]
        status, upload = self.asgi("POST", "/ingest", headers)
        self.assertEqual(status, 413)
        self.assertEqual(upload.calls, 0)

    def test_an_unauthenticated_body_on_an_open_path_is_capped_at_a_few_kib(self) -> None:
        headers = [(b"content-length", str(1024 * 1024).encode())]
        status, upload = self.asgi("POST", "/health", headers)
        self.assertEqual(status, 413)
        self.assertEqual(upload.calls, 0)

    def test_health_stays_open_without_a_token(self) -> None:
        self.assertEqual(self.client.get("/health").json(), {"ok": True})

    def test_the_gate_covers_every_route_except_health(self) -> None:
        # Default deny (0.13 S14): only the exact paths in ANONYMOUS_PATHS skip the gate, so a
        # route added later is covered without anyone remembering to list it.
        self.assertEqual(self.ingest_api.ANONYMOUS_PATHS, frozenset({"/health"}))
        sample = {"plan_id": "6f9619ff-8b86-d011-b42d-00c04fc964ff"}
        routes = [(method, route.path.format(**sample)) for route in self.ingest_api.app.routes
                  if isinstance(route, APIRoute) for method in route.methods]
        self.assertIn(("POST", "/ingest"), routes)
        for method, path in routes:
            if path == "/health":
                continue
            with self.subTest(method=method, path=path):
                status, upload = self.asgi(method, path, [(b"content-length", b"100")])
                self.assertEqual(status, 401)
                self.assertEqual(upload.calls, 0)

    def test_a_path_with_a_trailing_newline_cannot_skip_the_first_lock(self) -> None:
        # `/ingest%0A` arrives as path "/ingest\n" and Starlette's `^/ingest$` still routes it to
        # /ingest. It used to miss the gate's protected-path list: FastAPI read and parsed the body
        # before the route dependency answered 401.
        for path in ("/ingest\n", "/ingest/\n", "/plan/pending\n",
                     "/plan/6f9619ff-8b86-d011-b42d-00c04fc964ff/ack\n", "/health\n"):
            for method in ("POST", "GET"):
                with self.subTest(method=method, path=path):
                    headers = [(b"content-type", b"application/json"), (b"content-length", b"100")]
                    status, upload = self.asgi(method, path, headers)
                    self.assertEqual(status, 401)
                    self.assertEqual(upload.calls, 0, "the body must stay unread")

    def test_unknown_paths_need_the_token_first(self) -> None:
        self.assertEqual(self.client.get("/nope").status_code, 401)
        self.assertEqual(self.client.get("/health/").status_code, 401)
        authorized = {"Authorization": f"Bearer {self.token}"}
        self.assertEqual(self.client.get("/nope", headers=authorized).status_code, 404)

    def test_serve_hands_uvicorn_a_concurrency_limit_and_a_keep_alive_timeout(self) -> None:
        # uvicorn 0.48 has no header or read timeout; these are the limits it does have (0.13 S14).
        with mock.patch.dict(os.environ, {"KORE_INGEST_HOST": "", "KORE_BIND_ADDRESS": ""}), \
                mock.patch("uvicorn.run") as run:
            self.ingest_api.serve()
        run.assert_called_once()
        self.assertIs(run.call_args.args[0], self.ingest_api.app)
        kwargs = run.call_args.kwargs
        self.assertEqual((kwargs["host"], kwargs["port"]), ("127.0.0.1", 8081))
        self.assertEqual(kwargs["limit_concurrency"], 1024)
        self.assertEqual(kwargs["timeout_keep_alive"], 5)

    # -- 2. no interactive docs ---------------------------------------------------------------

    def test_docs_redoc_and_openapi_are_gone_with_and_without_a_token(self) -> None:
        for path in ("/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json"):
            for headers, expected in (({}, 401), ({"Authorization": f"Bearer {self.token}"}, 404)):
                with self.subTest(path=path, authenticated=bool(headers)):
                    self.assertEqual(self.client.get(path, headers=headers).status_code, expected)

    def test_the_app_object_has_no_docs_urls(self) -> None:
        app = self.ingest_api.app
        self.assertIsNone(app.docs_url)
        self.assertIsNone(app.redoc_url)
        self.assertIsNone(app.openapi_url)


if __name__ == "__main__":
    unittest.main()
