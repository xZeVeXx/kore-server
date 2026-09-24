"""scripts/kore-doctor gegen gestubbte Endpunkte (Spec E S6, Spec C §4.9, Plan 0.10 P2).

Bridge, Ingest und MCP sind kleine HTTP-Server auf 127.0.0.1, `hermes` ist ein Python-Skript. Jede
Abweichung muss zu Exit != 0 fuehren, und keine Ausgabe darf einen Geheimwert enthalten (die Tests
suchen nach den Werten selbst und nach jeder Hex-Folge ab 32 Zeichen).
"""
import importlib.machinery
import importlib.util
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import tempfile
import textwrap
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
DOCTOR = SCRIPTS / "kore-doctor"
CONTRACT = SCRIPTS.parent / "contract" / "app-required-capabilities.json"
HEX_SECRET = re.compile(r"[0-9a-fA-F]{32,}")


def load_doctor():
    loader = importlib.machinery.SourceFileLoader("kore_doctor", str(DOCTOR))
    spec = importlib.util.spec_from_loader("kore_doctor", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class StubServer:
    """HTTP-Stub; `routes[path]` ist eine Funktion (headers) -> (status, body-dict)."""

    def __init__(self, routes):
        self.routes = routes
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                route = stub.routes.get(self.path.split("?")[0])
                status, body = route(self.headers) if route else (404, {"error": "not found"})
                raw = json.dumps(body).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, args=(0.01,), daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


FAKE_HERMES = textwrap.dedent("""\
    #!{python}
    import json, sys
    from pathlib import Path
    config = json.loads((Path(__file__).resolve().parent / "fake-hermes.json").read_text())
    args = sys.argv[1:]
    if args == ["--version"]:
        print("Hermes Agent v0.21.4 (2026.9.21)")
        print("Project: /home/hermes/.hermes/hermes-agent")
        sys.exit(0)
    if args[:2] == ["-p", config["profile"]] and args[2:] == ["mcp", "test", "kore"]:
        print("  Testing 'kore'...")
        print("    Authorization: Bearer " + config["leak"])
        if config["mcp_exit"] == 0:
            print("  \\u2713 Connected (42ms)")
            print("  \\u2713 Tools discovered: 8")
        sys.exit(config["mcp_exit"])
    sys.exit(2)
    """)


class DoctorTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.bridge_token = secrets.token_hex(24)
        self.ingest_token = secrets.token_hex(24)
        self.mcp_token = secrets.token_hex(24)
        self.origin_secret = secrets.token_hex(32)
        self.contract = json.loads(CONTRACT.read_text(encoding="utf-8"))

        self.bridge_state = {
            "capabilities": list(self.contract) + ["momentum_safety_v1"],
            "api_version": 2,
            "prompt_transport": "stdin",
            "app_chat_toolsets": {"general": ["web", "skills", "memory"],
                                  "health": ["web", "skills", "memory", "kore"]},
            "open_without_token": False,
        }
        self.ingest_state = {"health_status": 200, "origin_status": 403}
        self.mcp_state = {"status": 401}

        def bridge_health(headers):
            state = self.bridge_state
            if headers.get("Authorization") != f"Bearer {self.bridge_token}" and not state["open_without_token"]:
                return 401, {"error": "unauthorized", "code": "unauthorized"}
            body = {
                "ok": True, "service": "kore-coach-bridge", "api_version": state["api_version"],
                "capabilities": sorted(state["capabilities"]), "health_context_fail_closed": True,
                "prompt_transport": state["prompt_transport"],
                "turn_buffer_ttl_hours": 24, "session_retention_days": 30, "active_turns": 0,
            }
            if "app_chat_toolsets" in state:
                body["app_chat_toolsets"] = state["app_chat_toolsets"]
            return 200, body

        def ingest_health(headers):
            if headers.get("Origin") is not None:
                return self.ingest_state["origin_status"], {"detail": "Forbidden origin"}
            return self.ingest_state["health_status"], {"ok": True}

        def mcp(headers):
            return self.mcp_state["status"], {"error": "invalid_token"}

        self.bridge = StubServer({"/health": bridge_health})
        self.ingest = StubServer({"/health": ingest_health})
        self.mcp = StubServer({"/mcp": mcp, "/mcp/": mcp})
        for server in (self.bridge, self.ingest, self.mcp):
            self.addCleanup(server.close)

        self.home = root / "home"
        (self.home / ".hermes" / "profiles" / "kore-coach").mkdir(parents=True)
        bin_dir = root / "bin"
        bin_dir.mkdir()
        self.hermes = bin_dir / "hermes"
        self.hermes.write_text(FAKE_HERMES.format(python=sys.executable))
        self.hermes.chmod(0o755)
        self.hermes_config = {"profile": "kore-coach", "mcp_exit": 0, "leak": self.mcp_token}
        self.write_hermes_config()

        self.bridge_env_values = {
            "KORE_BRIDGE_TOKEN": self.bridge_token,
            "KORE_BRIDGE_BIND_HOST": "127.0.0.1",
            "KORE_BRIDGE_PORT": str(self.bridge.port),
            "KORE_HERMES_BIN": str(self.hermes),
            "KORE_HERMES_HOME": str(self.home),
            "KORE_HERMES_PROFILE": "kore-coach",
            "KORE_INSTANCE_ID": "kore",
            "KORE_PLAN_ORIGIN_PRINCIPAL": "kore",
            "KORE_PLAN_ORIGIN_SECRET": self.origin_secret,
        }
        self.server_env_values = {
            "KORE_INGEST_TOKEN": self.ingest_token,
            "KORE_MCP_TOKEN": self.mcp_token,
            "KORE_MCP_CLIENT_ID": "my-hermes",
            "KORE_PLAN_ORIGIN_SECRET": self.origin_secret,
            "KORE_INSTANCE_ID": "kore",
            "KORE_PLAN_ORIGIN_PRINCIPAL": "kore",
            "KORE_BIND_ADDRESS": "127.0.0.1",
            "KORE_MCP_PORT": str(self.mcp.port),
        }
        self.bridge_env = root / "bridge.env"
        self.server_env = root / "server.env"
        self.write_envs()

    def write_hermes_config(self):
        (self.hermes.parent / "fake-hermes.json").write_text(json.dumps(self.hermes_config))

    def write_envs(self):
        for path, values in ((self.bridge_env, self.bridge_env_values),
                             (self.server_env, self.server_env_values)):
            lines = ["# Beispiel", ""] + [f"{key}={value}" for key, value in values.items()]
            path.write_text("\n".join(lines) + "\n")

    def run_doctor(self, *extra, bridge_only=False, ingest_url=True):
        args = [sys.executable, str(DOCTOR), "--bridge-env", str(self.bridge_env)]
        if bridge_only:
            args.append("--bridge-only")
        else:
            args += ["--server-env", str(self.server_env)]
            if ingest_url:
                args += ["--ingest-url", f"http://127.0.0.1:{self.ingest.port}"]
        args += list(extra)
        env = {key: value for key, value in os.environ.items()
               if not key.lower().endswith("_proxy")}
        process = subprocess.run(args, capture_output=True, text=True, timeout=60, env=env)
        output = process.stdout + process.stderr
        for value in (self.bridge_token, self.ingest_token, self.mcp_token, self.origin_secret):
            self.assertNotIn(value, output)
        self.assertIsNone(HEX_SECRET.search(output), output)
        return process.returncode, output


class DoctorEndToEndTests(DoctorTestCase):
    def test_a_healthy_installation_exits_0_and_names_the_hermes_version(self):
        code, output = self.run_doctor()
        self.assertEqual(code, 0, output)
        self.assertIn("Hermes Agent v0.21.4", output)
        self.assertIn("session_retention_days", output)
        self.assertNotIn("FEHLER", output)

    def test_bridge_only_needs_neither_server_env_nor_ingest(self):
        self.server_env.unlink()
        code, output = self.run_doctor(bridge_only=True)
        self.assertEqual(code, 0, output)

    def test_every_deviation_exits_non_zero(self):
        cases = [
            ("capability fehlt", lambda: self.bridge_state["capabilities"].remove("session_forget_v1"),
             "session_forget_v1"),
            ("api_version", lambda: self.bridge_state.update(api_version=1), "api_version"),
            ("argv", lambda: self.bridge_state.update(prompt_transport="argv-explicit-opt-in"),
             "prompt_transport"),
            ("terminal im Chat", lambda: self.bridge_state["app_chat_toolsets"]["general"].append("terminal"),
             "terminal"),
            ("hermes-Sammelname", lambda: self.bridge_state["app_chat_toolsets"]["health"].append("hermes-cli"),
             "hermes-cli"),
            ("Werkzeugliste fehlt", lambda: self.bridge_state.pop("app_chat_toolsets"), "app_chat_toolsets"),
            ("Bridge offen", lambda: self.bridge_state.update(open_without_token=True), "401"),
            ("Ingest krank", lambda: self.ingest_state.update(health_status=500), "Ingest"),
            ("Origin durchgelassen", lambda: self.ingest_state.update(origin_status=200), "Origin"),
            ("MCP ohne Token offen", lambda: self.mcp_state.update(status=200), "MCP"),
        ]
        for name, change, needle in cases:
            with self.subTest(name):
                self.setUp()
                change()
                code, output = self.run_doctor()
                self.assertNotEqual(code, 0, output)
                self.assertIn(needle, output)

    def test_configuration_deviations_exit_non_zero(self):
        cases = [
            ("Origin-Geheimnis verschieden",
             lambda: self.server_env_values.update(KORE_PLAN_ORIGIN_SECRET=secrets.token_hex(32))),
            ("Instanz verschieden", lambda: self.server_env_values.update(KORE_INSTANCE_ID="andere")),
            ("Principal verschieden",
             lambda: self.bridge_env_values.update(KORE_PLAN_ORIGIN_PRINCIPAL="andere")),
            ("falsches Bridge-Token", lambda: self.bridge_env_values.update(KORE_BRIDGE_TOKEN=secrets.token_hex(24))),
            ("Profil fehlt", lambda: self.bridge_env_values.update(KORE_HERMES_PROFILE="fehlt")),
            ("Hermes fehlt", lambda: self.bridge_env_values.update(KORE_HERMES_BIN="/nirgends/hermes")),
            ("Bridge aus", lambda: self.bridge_env_values.update(KORE_BRIDGE_PORT=str(free_port()))),
        ]
        for name, change in cases:
            with self.subTest(name):
                self.setUp()
                change()
                self.write_envs()
                code, output = self.run_doctor()
                self.assertNotEqual(code, 0, output)

    def test_mcp_test_failures_exit_non_zero_and_name_the_profile_example(self):
        for exit_code in (1, 3):
            with self.subTest(exit_code=exit_code):
                self.hermes_config["mcp_exit"] = exit_code
                self.write_hermes_config()
                code, output = self.run_doctor()
                self.assertNotEqual(code, 0, output)
                self.assertIn("kore-coach-profile.example.yaml", output)

    def test_unreadable_server_env_without_bridge_only_is_a_deviation(self):
        self.server_env.unlink()
        code, output = self.run_doctor()
        self.assertNotEqual(code, 0, output)
        self.assertIn("--bridge-only", output)

    def test_placeholder_values_are_deviations(self):
        self.bridge_env_values["KORE_BRIDGE_TOKEN"] = "change-me-bridge-token"
        self.write_envs()
        code, output = self.run_doctor()
        self.assertNotEqual(code, 0, output)
        self.assertIn("KORE_BRIDGE_TOKEN", output)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class DoctorUnitTests(unittest.TestCase):
    def setUp(self):
        self.doctor = load_doctor()

    def test_mcp_reachable_on_a_lan_address_is_a_deviation(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            port = listener.getsockname()[1]
            report = self.doctor.Report(secrets_to_hide=())
            self.doctor.check_mcp_not_on_lan(report, ["127.0.0.1"], port)
            self.assertEqual(report.failures, 1)
        report = self.doctor.Report(secrets_to_hide=())
        self.doctor.check_mcp_not_on_lan(report, ["127.0.0.1"], free_port())
        self.assertEqual(report.failures, 0)

    def test_lan_hosts_are_concrete_non_loopback_addresses(self):
        hosts = self.doctor.lan_hosts("127.0.0.1", "http://127.0.0.1:8081")
        self.assertEqual(hosts, [])
        hosts = self.doctor.lan_hosts("10.0.0.25", "http://10.0.0.40:8081")
        self.assertEqual(hosts, ["10.0.0.25", "10.0.0.40"])
        self.assertEqual(self.doctor.lan_hosts("0.0.0.0", None), [])

    def test_env_file_parsing_ignores_comments_and_strips_quotes(self):
        with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False) as handle:
            handle.write('# Kommentar\n\nA=1\nB="zwei"\nC=\'drei\'\nexport D=4\nkaputt\n')
        self.addCleanup(os.unlink, handle.name)
        self.assertEqual(self.doctor.read_env_file(handle.name), {"A": "1", "B": "zwei", "C": "drei", "D": "4"})

    def test_report_hides_known_secrets_even_in_unexpected_text(self):
        secret = secrets.token_hex(24)
        report = self.doctor.Report(secrets_to_hide=(secret,))
        self.assertNotIn(secret, report.clean(f"Fehler: {secret}"))


if __name__ == "__main__":
    unittest.main()
