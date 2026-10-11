"""scripts/kore-doctor gegen gestubbte Endpunkte.

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
import stat
import tempfile
import textwrap
import threading
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
DOCTOR = SCRIPTS / "kore-doctor"
CONTRACT = SCRIPTS.parent / "contract" / "app-required-capabilities.json"
SOUL = SCRIPTS.parent / "hermes" / "kore-app-chat-soul.md"
BRIDGE_DIR = SCRIPTS.parent / "coach-bridge"
HEX_SECRET = re.compile(r"[0-9a-fA-F]{32,}")
sys.path.insert(0, str(BRIDGE_DIR))
import pairing  # noqa: E402


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
    import json, os, shutil, sys
    from pathlib import Path
    config = json.loads((Path(__file__).resolve().parent / "fake-hermes.json").read_text())
    args = sys.argv[1:]
    # Wie Hermes v0.21.5 mit einem Anbieter-Plugin, das ein lokales Programm startet: fehlt das
    # Programm auf dem PATH des Prozesses, steht beim Start eine Warnung auf stderr.
    if config.get("needs") and shutil.which(config["needs"]) is None:
        print("Example Provider: example-cli is not installed "
              "(no `" + config["needs"] + "` on PATH).", file=sys.stderr)
    with open(Path(__file__).resolve().parent / "fake-hermes-env.json", "w") as handle:
        json.dump(dict(os.environ), handle)
    if args in (["--version"], ["-p", config["profile"], "--version"]):
        print("Hermes Agent v0.21.4 (2026.9.21)")
        print("Project: /srv/coach/.hermes/hermes-agent")
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
            "capabilities": list(self.contract) + ["momentum_safety_v1", "health_web_opt_in_v1"],
            "api_version": 2,
            "prompt_transport": "stdin",
            "app_chat_toolsets": {"general": ["web", "skills", "memory"],
                                  "health": ["skills", "memory", "kore"],
                                  "health_with_web": ["web", "skills", "memory", "kore"]},
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
        self.profile_dir = self.home / ".hermes" / "profiles" / "kore-coach"
        self.profile_dir.mkdir(parents=True)
        (self.profile_dir / "config.yaml").write_text(
            "model:\n  default: example-model\n  provider: example-provider\n"
            "  base_url: ''\nauxiliary:\n  title_generation:\n    provider: auto\n"
            "plugins:\n  enabled:\n    - example-provider\n  disabled: []\n"
        )
        (self.profile_dir / "SOUL.md").write_text("## Coach\n\nAllgemeines.\n\n" + SOUL.read_text())
        # `example-cli` liegt in einem eigenen Ordner, der nur ueber KORE_HERMES_CHILD_PATH dazukommt.
        self.program_dir = root / "provider-bin"
        self.program_dir.mkdir()
        program = self.program_dir / "example-cli"
        program.write_text("#!/bin/sh\nexit 0\n")
        program.chmod(0o755)
        bin_dir = root / "bin"
        bin_dir.mkdir()
        self.hermes = bin_dir / "hermes"
        self.hermes.write_text(FAKE_HERMES.format(python=sys.executable))
        self.hermes.chmod(0o755)
        self.hermes_config = {"profile": "kore-coach", "mcp_exit": 0, "leak": self.mcp_token,
                              "needs": "example-cli"}
        self.write_hermes_config()

        # Zustandsordner der Kopplung, wie `init-state` ihn anlegt.
        self.state_dir = root / "state"
        self.state_dir.mkdir()
        os.chmod(self.state_dir, 0o750)
        (self.state_dir / "server_id").write_text("a1b2c3d4\n")
        os.chmod(self.state_dir / "server_id", 0o600)

        self.bridge_env_values = {
            "KORE_STATE_DIR": str(self.state_dir),
            "KORE_BRIDGE_TOKEN": self.bridge_token,
            "KORE_BRIDGE_BIND_HOST": "127.0.0.1",
            "KORE_BRIDGE_PORT": str(self.bridge.port),
            "KORE_HERMES_BIN": str(self.hermes),
            "KORE_HERMES_HOME": str(self.home),
            "KORE_HERMES_CHILD_PATH": f"{self.program_dir}:/usr/bin:/bin",
            "KORE_HERMES_PROFILE": "kore-coach",
            "KORE_INSTANCE_ID": "kore",
            "KORE_PLAN_ORIGIN_PRINCIPAL": "kore",
            "KORE_PLAN_ORIGIN_SECRET": self.origin_secret,
        }
        self.server_env_values = {
            "KORE_STATE_DIR": str(self.state_dir),
            "KORE_STATE_GID": str(os.stat(self.state_dir).st_gid),
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

    def run_doctor(self, *extra, bridge_only=False, ingest_url=True, own_path_prefix=None):
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
        if own_path_prefix is not None:
            env["PATH"] = f"{own_path_prefix}:{env.get('PATH', '')}"
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

    def test_the_health_web_opt_in_is_reported_and_the_default_still_has_no_web(self):
        code, output = self.run_doctor()
        self.assertEqual(code, 0, output)
        self.assertIn("health_web_opt_in_v1", output)
        self.assertIn("health_web_allowed", output)
        self.assertNotIn("FEHLER", output)

    def test_a_bridge_without_the_opt_in_is_still_healthy(self):
        # Die Faehigkeit ist optional (nicht im Pflichtvertrag): aeltere Bridges bleiben ohne Fehler.
        self.bridge_state["capabilities"].remove("health_web_opt_in_v1")
        self.bridge_state["app_chat_toolsets"].pop("health_with_web")
        code, output = self.run_doctor()
        self.assertEqual(code, 0, output)
        self.assertNotIn("FEHLER", output)

    def test_the_opt_in_list_must_stay_inside_health_plus_web(self):
        for label, change, needle in (
            ("zusaetzliches Werkzeug",
             lambda: self.bridge_state["app_chat_toolsets"]["health_with_web"].append("session_search"),
             "health_with_web"),
            ("Fähigkeit ohne Liste",
             lambda: self.bridge_state["app_chat_toolsets"].pop("health_with_web"),
             "health_with_web"),
            ("Liste ohne Fähigkeit",
             lambda: self.bridge_state["capabilities"].remove("health_web_opt_in_v1"),
             "health_web_opt_in_v1"),
        ):
            with self.subTest(label):
                self.setUp()
                change()
                code, output = self.run_doctor()
                self.assertNotEqual(code, 0, output)
                self.assertIn(needle, output)

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
            ("web im Health-Scope", lambda: self.bridge_state["app_chat_toolsets"]["health"].insert(0, "web"),
             "Health-Scope enthaelt web"),
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


class DoctorHermesChildEnvTests(DoctorTestCase):
    """Das Programm eines Anbieter-Plugins lag nur auf dem PATH der Shell, nicht auf dem Kind-PATH
    der Bridge."""

    def hermes_env(self) -> dict:
        return json.loads((self.hermes.parent / "fake-hermes-env.json").read_text())

    def test_healthy_run_names_the_provider_and_uses_exactly_the_child_env(self):
        code, output = self.run_doctor(bridge_only=True)

        self.assertEqual(code, 0, output)
        self.assertIn("Modell-Anbieter des Profils: example-provider", output)
        env = self.hermes_env()
        self.assertEqual(env["PATH"], f"{self.program_dir}:/usr/bin:/bin")
        self.assertEqual(env["HOME"], str(self.home))
        self.assertNotIn("KORE_BRIDGE_TOKEN", env)
        allowed = {"HOME", "PATH", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "REQUESTS_CA_BUNDLE",
                   "SSL_CERT_DIR", "SSL_CERT_FILE"}
        # macOS setzt __CF_USER_TEXT_ENCODING in jedem Prozess selbst.
        self.assertEqual(set(env) - allowed - {"__CF_USER_TEXT_ENCODING"}, set())

    def test_program_only_on_the_doctors_own_path_is_a_deviation(self):
        for name, child_path in (("eigener Kind-PATH", "/usr/bin:/bin"), ("Standard der Bridge", None)):
            with self.subTest(name):
                self.setUp()
                if child_path is None:
                    self.bridge_env_values.pop("KORE_HERMES_CHILD_PATH")
                else:
                    self.bridge_env_values["KORE_HERMES_CHILD_PATH"] = child_path
                self.write_envs()

                code, output = self.run_doctor(bridge_only=True, own_path_prefix=str(self.program_dir))

                self.assertNotEqual(code, 0, output)
                self.assertIn("`example-cli`", output)
                self.assertIn("KORE_HERMES_CHILD_PATH", output)
                # Der Hinweis nennt den Ordner, in dem der Doctor das Programm selbst findet.
                self.assertIn(f"KORE_HERMES_CHILD_PATH={self.program_dir}:", output)

    def test_a_plugin_provider_is_named_without_guessing_its_program(self):
        # 0.13 S14: kein fest eingebauter Anbieter mehr. Ob ein Plugin ein Programm braucht, sagt
        # nur Hermes selbst (Warnung beim Start); ohne Warnung ist nichts abweichend.
        self.hermes_config["needs"] = None
        self.write_hermes_config()
        self.bridge_env_values["KORE_HERMES_CHILD_PATH"] = "/usr/bin:/bin"
        self.write_envs()

        code, output = self.run_doctor(bridge_only=True)

        self.assertEqual(code, 0, output)
        self.assertIn("Modell-Anbieter des Profils: example-provider (aus dem Plugin example-provider)",
                      output)
        self.assertIn("Kind-PATH", output)

    def test_a_provider_without_plugins_is_only_named(self):
        (self.profile_dir / "config.yaml").write_text("model:\n  provider: openrouter\n")
        self.hermes_config["needs"] = None
        self.write_hermes_config()

        code, output = self.run_doctor(bridge_only=True)

        self.assertEqual(code, 0, output)
        self.assertIn("Modell-Anbieter des Profils: openrouter.", output)
        self.assertNotIn("Plugin", output)


class DoctorS11aTests(DoctorTestCase):
    """0.13 S11a: Token-Laenge und skills.inline_shell im Profil."""

    def test_tokens_shorter_than_32_characters_are_deviations(self):
        for key in ("KORE_BRIDGE_TOKEN", "KORE_INGEST_TOKEN", "KORE_MCP_TOKEN"):
            with self.subTest(key):
                self.setUp()
                short = "k" * 31
                if key == "KORE_BRIDGE_TOKEN":
                    self.bridge_token = short
                    self.bridge_env_values[key] = short
                elif key == "KORE_INGEST_TOKEN":
                    self.ingest_token = short
                    self.server_env_values[key] = short
                else:
                    self.mcp_token = short
                    self.server_env_values[key] = short
                    self.hermes_config["leak"] = short
                    self.write_hermes_config()
                self.write_envs()

                code, output = self.run_doctor()

                self.assertNotEqual(code, 0, output)
                self.assertIn(f"{key} ist kuerzer als 32 Zeichen", output)

    def test_a_long_enough_token_is_reported_as_ok(self):
        code, output = self.run_doctor()
        self.assertEqual(code, 0, output)
        self.assertIn("KORE_BRIDGE_TOKEN hat mindestens 32 Zeichen", output)

    def test_inline_shell_in_the_profile_is_a_deviation(self):
        config = self.profile_dir / "config.yaml"
        config.write_text(config.read_text() + "skills:\n  inline_shell: true\n")
        code, output = self.run_doctor(bridge_only=True)
        self.assertNotEqual(code, 0, output)
        self.assertIn("[FEHLER] skills.inline_shell", output)

    def test_inline_shell_off_or_missing_is_ok(self):
        for extra in ("", "skills:\n  inline_shell: false\n"):
            with self.subTest(extra=extra):
                config = self.profile_dir / "config.yaml"
                config.write_text("model:\n  provider: example-provider\n" + extra)
                code, output = self.run_doctor(bridge_only=True)
                self.assertEqual(code, 0, output)
                self.assertIn("[OK]     skills.inline_shell", output)

    def test_doctor_and_bridge_judge_inline_shell_alike(self):
        process = subprocess.run(
            [sys.executable, "-c",
             "import json, bridge\n"
             "from test_hardening import INLINE_SHELL_SAFE, INLINE_SHELL_UNSAFE\n"
             "texts = list(INLINE_SHELL_SAFE.values()) + list(INLINE_SHELL_UNSAFE.values())\n"
             "print(json.dumps([[t, bridge.inline_shell_config_error(t) is None] for t in texts]))"],
            capture_output=True, text=True, timeout=30, cwd=BRIDGE_DIR,
            env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PYTHONPATH": str(BRIDGE_DIR),
                 "HOME": self.tmp.name},
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        verdicts = json.loads(process.stdout)
        self.assertGreater(len(verdicts), 20)
        doctor = load_doctor()
        for text, bridge_safe in verdicts:
            with self.subTest(text=text):
                self.assertEqual(doctor.inline_shell_config_error(text) is None, bridge_safe)


class DoctorSoulTests(DoctorTestCase):
    """Die SOUL.md des Profils trug noch den Kore-Abschnitt vom Juni (kore-plan ohne
    Plan-ID, Fingerabdruck und Removal-Listen); die App verwarf den Block ohne Karte."""

    def test_an_outdated_kore_section_is_a_deviation(self):
        (self.profile_dir / "SOUL.md").write_text(
            "## Coach\n\n## Kore-App-Chat (Coach-Tab) — Formatierung, Daten, Pläne\n\nalt\n")
        code, output = self.run_doctor(bridge_only=True)
        self.assertNotEqual(code, 0, output)
        self.assertIn("veralteten Kore-Abschnitt", output)

    def test_a_missing_kore_section_is_a_deviation(self):
        (self.profile_dir / "SOUL.md").unlink()
        code, output = self.run_doctor(bridge_only=True)
        self.assertNotEqual(code, 0, output)
        self.assertIn("keinen Kore-Abschnitt", output)

    def test_rewrapped_whitespace_still_counts_as_current(self):
        wrapped = " \n".join(SOUL.read_text().split(" "))
        (self.profile_dir / "SOUL.md").write_text(wrapped)
        code, output = self.run_doctor(bridge_only=True)
        self.assertEqual(code, 0, output)


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

    def test_child_env_matches_the_bridge_exactly(self):
        parent = {"HOME": "/home/andere", "PATH": "/eigener/pfad", "LANG": "de_DE.UTF-8",
                  "TZ": "Europe/Berlin", "KORE_BRIDGE_TOKEN": "t" * 48, "SECRET_SOMETHING": "x"}
        cases = {
            "leer": {},
            "alles gesetzt": {"KORE_HERMES_HOME": "/srv/hermes", "KORE_HERMES_CHILD_PATH": "/a:/b",
                              "LC_ALL": "C.UTF-8"},
            "leerer Kind-PATH": {"KORE_HERMES_CHILD_PATH": ""},
        }
        for name, bridge_env in cases.items():
            with self.subTest(name):
                process = subprocess.run(
                    [sys.executable, "-c",
                     "import json, bridge; print(json.dumps(bridge.hermes_child_env()))"],
                    capture_output=True, text=True, timeout=30, cwd=BRIDGE_DIR,
                    # PYTHONCOERCECLOCALE=0: fehlt die Locale (etwa de_DE auf CI-Runnern), setzt Python sonst
                    # selbst LC_CTYPE=C.UTF-8 in die eigene Umgebung — das ist kein Verhalten der Bridge.
                    env={**parent, **bridge_env, "PYTHONPATH": str(BRIDGE_DIR), "PYTHONCOERCECLOCALE": "0"},
                )
                self.assertEqual(process.returncode, 0, process.stderr)
                expected = json.loads(process.stdout)
                self.assertEqual(self.doctor.hermes_child_env(bridge_env, parent), expected)

    def test_provider_is_read_only_from_the_model_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config.yaml"
            config.write_text("auxiliary:\n  provider: auto\nmodel:\n  default: example-model\n"
                              "  provider: 'example-provider' # Abo\n"
                              "toolsets:\n  - web\n")
            self.assertEqual(self.doctor.profile_model_provider(config), "example-provider")
            config.write_text("model:\n  provider: $(rm -rf /)\n")
            self.assertIsNone(self.doctor.profile_model_provider(config))
            self.assertIsNone(self.doctor.profile_model_provider(Path(tmp) / "fehlt.yaml"))

    def test_enabled_plugins_are_read_only_from_the_plugins_block(self):
        cases = {
            "Blockliste": ("plugins:\n  disabled: [x]\n  enabled:\n    - homeassistant\n"
                           "    - 'example-provider'  # Anbieter\nmodel:\n  provider: x\n",
                           ["homeassistant", "example-provider"]),
            "Flow-Liste": ("plugins:\n  enabled: [a-plugin, \"b.plugin\"]\n", ["a-plugin", "b.plugin"]),
            "leer": ("plugins:\n  enabled: []\n", []),
            "anderer Block": ("other:\n  enabled:\n    - nope\n", []),
            "unsicherer Name": ("plugins:\n  enabled:\n    - $(rm -rf /)\n    - ok\n", ["ok"]),
        }
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config.yaml"
            for name, (text, expected) in cases.items():
                with self.subTest(name):
                    config.write_text(text)
                    self.assertEqual(self.doctor.profile_enabled_plugins(config), expected)
            self.assertEqual(self.doctor.profile_enabled_plugins(Path(tmp) / "fehlt.yaml"), [])

    def test_no_provider_to_program_table_is_built_in(self):
        # Ob ein Anbieter ein Programm braucht, meldet Hermes selbst; der Doctor raet nicht.
        self.assertFalse(hasattr(self.doctor, "PROVIDER_PROGRAMS"))

    def test_without_the_soul_file_next_to_the_doctor_the_check_is_skipped(self):
        report = self.doctor.Report(secrets_to_hide=(), stream=open(os.devnull, "w"))
        self.addCleanup(report.stream.close)
        self.doctor.SOUL_SECTION = Path("/nirgends/kore-app-chat-soul.md")
        self.doctor.check_soul(report, Path("/nirgends"), "coach")
        self.assertEqual(report.failures, 0)

    def test_report_hides_known_secrets_even_in_unexpected_text(self):
        secret = secrets.token_hex(24)
        report = self.doctor.Report(secrets_to_hide=(secret,))
        self.assertNotIn(secret, report.clean(f"Fehler: {secret}"))


class DoctorStateDirCheckTests(DoctorTestCase):
    def test_a_missing_state_dir_is_a_deviation_that_names_init_state(self):
        (self.state_dir / "server_id").unlink()
        self.state_dir.rmdir()
        code, output = self.run_doctor()
        self.assertEqual(code, 1, output)
        self.assertIn("Zustandsordner der Kopplung fehlt", output)
        self.assertIn("init-state", output)

    def test_wrong_modes_are_deviations(self):
        cases = {
            "Ordner 0700 (Ingest koennte nicht lesen)": (self.state_dir, 0o700, "0700 statt 0750"),
            "Ordner 0755 (andere duerften lesen)": (self.state_dir, 0o755, "0755 statt 0750"),
        }
        for name, (path, mode, expected) in cases.items():
            with self.subTest(name):
                os.chmod(path, mode)
                code, output = self.run_doctor()
                self.assertEqual(code, 1, output)
                self.assertIn(expected, output)
        os.chmod(self.state_dir, 0o750)
        devices = self.state_dir / "devices.json"
        devices.write_text('{"devices": []}')
        for mode, ok in ((0o644, False), (0o600, False), (0o640, True)):
            with self.subTest(devices=oct(mode)):
                os.chmod(devices, mode)
                code, output = self.run_doctor()
                self.assertEqual(code, 0 if ok else 1, output)
        pending = self.state_dir / "pairing.json"
        pending.write_text("{}")
        os.chmod(pending, 0o640)
        code, output = self.run_doctor()
        self.assertEqual(code, 1, output)
        self.assertIn("pairing.json ist 0640 statt 0600", output)

    def test_state_gid_must_be_the_directory_group(self):
        actual = os.stat(self.state_dir).st_gid
        self.server_env_values["KORE_STATE_GID"] = str(actual + 1)
        self.write_envs()
        code, output = self.run_doctor()
        self.assertEqual(code, 1, output)
        self.assertIn("KORE_STATE_GID stimmt nicht", output)
        self.assertIn(f"KORE_STATE_GID={actual}", output)
        self.server_env_values["KORE_STATE_GID"] = str(actual)
        self.write_envs()
        code, output = self.run_doctor()
        self.assertEqual(code, 0, output)
        self.assertIn("ist die Gruppe des Zustandsordners", output)

    def test_unset_state_gid_is_only_skipped(self):
        del self.server_env_values["KORE_STATE_GID"]
        self.write_envs()
        code, output = self.run_doctor()
        self.assertEqual(code, 0, output)
        self.assertIn("KORE_STATE_GID nicht gesetzt", output)

    def test_bridge_only_still_checks_the_directory(self):
        os.chmod(self.state_dir, 0o777)
        code, output = self.run_doctor(bridge_only=True)
        self.assertEqual(code, 1, output)
        self.assertIn("0777 statt 0750", output)


class PairingCliCase(unittest.TestCase):
    """kore-doctor init-state/pair/devices/revoke als Prozess, mit eigenem Zustandsordner."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.state_dir = self.root / "state"
        self.bridge_env = self.root / "bridge.env"
        self.server_env = self.root / "server.env"
        self.empty_path = self.root / "empty-bin"
        self.empty_path.mkdir()
        self.write_envs()

    def write_envs(self, bridge_host="10.0.0.10", bind_address="10.0.0.10", port="8090"):
        self.bridge_env.write_text(
            f"KORE_BRIDGE_BIND_HOST={bridge_host}\nKORE_BRIDGE_PORT={port}\n"
            f"KORE_STATE_DIR={self.state_dir}\n")
        self.server_env.write_text(f"KORE_BIND_ADDRESS={bind_address}\n")

    def run_cli(self, *args, path=None, with_envs=True):
        command = [sys.executable, str(DOCTOR), *args[:1]]
        if with_envs:
            command += ["--bridge-env", str(self.bridge_env), "--server-env", str(self.server_env)]
        command += list(args[1:])
        env = {key: value for key, value in os.environ.items()
               if not key.lower().endswith("_proxy") and key != "KORE_STATE_DIR"}
        env["PATH"] = str(self.empty_path) if path is None else path
        process = subprocess.run(command, capture_output=True, text=True, timeout=60, env=env)
        return process.returncode, process.stdout, process.stderr

    def link_of(self, output):
        lines = [line for line in output.splitlines() if line.startswith("kore://")]
        self.assertEqual(len(lines), 1, output)
        return lines[0]

    def store(self):
        return pairing.PairingStore(str(self.state_dir))


class PairLinkTests(PairingCliCase):
    def test_link_format_and_percent_encoding(self):
        code, out, err = self.run_cli("pair")
        self.assertEqual(code, 0, err)
        link = self.link_of(out)
        self.assertRegex(link, r"\Akore://pair\?v=1&bridge=[^&]+&ingest=[^&]+&code=[A-Z2-7]{20}&sid=[0-9a-f]{8}\Z")
        self.assertIn("bridge=http%3A%2F%2F10.0.0.10%3A8090&ingest=http%3A%2F%2F10.0.0.10%3A8081&", link)
        parts = urllib.parse.urlsplit(link)
        self.assertEqual((parts.scheme, parts.netloc), ("kore", "pair"))
        query = urllib.parse.parse_qs(parts.query, strict_parsing=True)
        self.assertEqual(query["v"], ["1"])
        self.assertEqual(query["bridge"], ["http://10.0.0.10:8090"])
        self.assertEqual(query["ingest"], ["http://10.0.0.10:8081"])
        self.assertEqual(query["sid"], [self.store().server_id()])

    def test_hint_in_german_and_english(self):
        code, out, err = self.run_cli("pair")
        self.assertEqual(code, 0, err)
        self.assertIn("Öffne diesen Link auf deinem iPhone — gilt 10 Minuten und nur einmal.", out)
        self.assertIn("Open this link on your iPhone — valid for 10 minutes, once.", out)

    def test_the_code_in_the_link_is_redeemable_exactly_once(self):
        _, out, _ = self.run_cli("pair")
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.link_of(out)).query)
        result = self.store().redeem(query["code"][0], "iPhone", "10.0.0.20")
        self.assertEqual(result["server_id"], query["sid"][0])
        with self.assertRaises(pairing.PairingFailed):
            self.store().redeem(query["code"][0], "iPhone", "10.0.0.20")

    def test_urls_come_from_the_env_files_unless_given(self):
        self.write_envs(bridge_host="10.0.0.10", bind_address="192.168.1.5", port="9000")
        _, out, _ = self.run_cli("pair")
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.link_of(out)).query)
        self.assertEqual(query["bridge"], ["http://10.0.0.10:9000"])
        self.assertEqual(query["ingest"], ["http://192.168.1.5:8081"])
        _, out, _ = self.run_cli("pair", "--bridge-url", "https://172.16.4.2:8443",
                                 "--ingest-url", "http://10.1.2.3:9999/")
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.link_of(out)).query)
        self.assertEqual(query["bridge"], ["https://172.16.4.2:8443"])
        self.assertEqual(query["ingest"], ["http://10.1.2.3:9999"])

    def test_env_derived_tailscale_bind_address_is_refused_with_the_hint(self):
        self.write_envs(bridge_host="100.64.0.10", bind_address="192.168.1.5")
        code, _, err = self.run_cli("pair")
        self.assertEqual(code, 1)
        self.assertIn("tailscale serve", err)

    def test_tailnet_https_urls_pass_through_to_the_link(self):
        code, out, err = self.run_cli("pair", "--bridge-url", "https://my-host.tail1234.ts.net:8443",
                                      "--ingest-url", "https://my-host.tail1234.ts.net",
                                      "--state-dir", str(self.state_dir))
        self.assertEqual(code, 0, err)
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.link_of(out)).query)
        self.assertEqual(query["bridge"], ["https://my-host.tail1234.ts.net:8443"])
        self.assertEqual(query["ingest"], ["https://my-host.tail1234.ts.net"])

    def test_flags_work_without_any_env_file(self):
        code, out, err = self.run_cli("pair", "--bridge-url", "http://10.0.0.10:8090",
                                      "--ingest-url", "http://10.0.0.10:8081",
                                      "--state-dir", str(self.state_dir))
        self.assertEqual(code, 0, err)
        self.assertEqual(len(self.store().list_devices()), 0)
        self.assertTrue((self.state_dir / "pairing.json").is_file())

    def test_public_hosts_hostnames_and_odd_urls_are_rejected_without_burning_a_code(self):
        self.state_dir.mkdir(mode=0o750)
        first = self.store().create_code()["code"]
        before = (self.state_dir / "pairing.json").read_text()
        bad = ["http://203.0.113.7:8090", "http://198.51.100.1", "http://example.com:8090",
               "http://127.0.0.1:8090", "http://0.0.0.0:8090", "http://[::1]:8090",
               "http://172.32.0.1:8090", "http://100.128.0.1:8090", "http://100.63.255.1:8090",
               "ftp://10.0.0.10", "javascript:alert(1)", "http://user:pw@10.0.0.10:8090",
               "http://10.0.0.10:8090/path", "http://10.0.0.10:8090/?x=1", "http://10.0.0.10:99999",
               "", "10.0.0.10:8090", "http://my-host.tail1234.ts.net:8090", "https://example.com:8443",
               "https://my-host.tail1234.ts.net.example.com"]
        for url in bad:
            for flag in ("--bridge-url", "--ingest-url"):
                with self.subTest(url=url, flag=flag):
                    other = "--ingest-url" if flag == "--bridge-url" else "--bridge-url"
                    code, out, err = self.run_cli("pair", flag, url, other, "http://10.0.0.10:8081")
                    self.assertEqual(code, 1, out)
                    self.assertIn("FEHLER", err)
                    self.assertNotIn("kore://", out + err)
        self.assertEqual((self.state_dir / "pairing.json").read_text(), before)
        self.assertIsNotNone(first)

    def test_loopback_defaults_say_what_to_configure(self):
        self.write_envs(bridge_host="127.0.0.1", bind_address="127.0.0.1")
        code, out, err = self.run_cli("pair")
        self.assertEqual(code, 1)
        self.assertIn("Bridge-Adresse", err)
        self.assertIn("--bridge-url", err)
        self.write_envs(bridge_host="10.0.0.10", bind_address="127.0.0.1")
        code, out, err = self.run_cli("pair")
        self.assertEqual(code, 1)
        self.assertIn("Ingest-Adresse", err)
        self.assertIn("KORE_BIND_ADDRESS", err + " KORE_BIND_ADDRESS")

    def test_name_labels_the_next_device(self):
        _, out, _ = self.run_cli("pair", "--name", "Wohnzimmer iPad")
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.link_of(out)).query)
        result = self.store().redeem(query["code"][0], "iPhone von der App", "10.0.0.20")
        names = {d["id"]: d["name"] for d in self.store().list_devices()}
        self.assertEqual(names[result["device_id"]], "Wohnzimmer iPad")
        # Ohne --name zaehlt der Name der App.
        _, out, _ = self.run_cli("pair")
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.link_of(out)).query)
        result = self.store().redeem(query["code"][0], "iPhone von der App", "10.0.0.20")
        names = {d["id"]: d["name"] for d in self.store().list_devices()}
        self.assertEqual(names[result["device_id"]], "iPhone von der App")

    def test_pair_creates_the_state_dir_with_the_contract_modes(self):
        old = os.umask(0o077)
        try:
            code, _, err = self.run_cli("pair")
        finally:
            os.umask(old)
        self.assertEqual(code, 0, err)
        mode = lambda name: stat.S_IMODE(os.stat(self.state_dir / name).st_mode)  # noqa: E731
        self.assertEqual(stat.S_IMODE(os.stat(self.state_dir).st_mode), 0o750)
        self.assertEqual((mode("pairing.json"), mode("server_id")), (0o600, 0o600))

    def test_unusable_state_dir_is_a_clear_error(self):
        self.state_dir.write_text("keine Ordner")
        code, out, err = self.run_cli("pair")
        self.assertEqual(code, 1)
        self.assertIn("Zustandsordner", err)
        self.assertNotIn("kore://", out)


class PairQrTests(PairingCliCase):
    def test_without_qrencode_the_link_is_still_printed_with_an_install_hint(self):
        code, out, err = self.run_cli("pair")  # PATH leer
        self.assertEqual(code, 0, err)
        self.link_of(out)
        self.assertIn("qrencode nicht gefunden", out)
        self.assertIn("brew install qrencode", out)
        self.assertIn("apt install qrencode", out)

    def test_with_qrencode_the_qr_comes_first_and_the_link_stays_off_argv(self):
        argv_log = self.root / "qrencode-argv"
        script = self.empty_path / "qrencode"
        script.write_text(f"#!/bin/sh\nprintf '%s ' \"$@\" > {argv_log}\nprintf 'QR<'\n/bin/cat\nprintf '>\\n'\n")
        script.chmod(0o755)
        code, out, err = self.run_cli("pair")
        self.assertEqual(code, 0, err)
        link = self.link_of(out)
        self.assertIn(f"QR<{link}>", out)  # der QR enthaelt genau den Link
        self.assertNotIn("qrencode nicht gefunden", out)
        self.assertEqual(argv_log.read_text().split(), ["-t", "ANSIUTF8"])  # nie der Link in der Prozessliste

    def test_a_failing_qrencode_falls_back_to_the_link(self):
        script = self.empty_path / "qrencode"
        script.write_text("#!/bin/sh\n/bin/cat >/dev/null\nexit 1\n")
        script.chmod(0o755)
        code, out, err = self.run_cli("pair")
        self.assertEqual(code, 0, err)
        self.link_of(out)
        self.assertIn("qrencode schlug fehl", out)


class DevicesRevokeTests(PairingCliCase):
    def pair_device(self, name):
        _, out, _ = self.run_cli("pair")
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.link_of(out)).query)
        return self.store().redeem(query["code"][0], name, "10.0.0.20")

    def test_devices_lists_id_name_times_and_never_secrets(self):
        self.assertEqual(self.run_cli("devices")[1].strip(), "Keine Geraete gekoppelt.")
        first = self.pair_device("iPhone")
        second = self.pair_device("iPad")
        self.store().touch(second["device_id"], now=1_800_000_000.0)
        code, out, err = self.run_cli("devices")
        self.assertEqual(code, 0, err)
        lines = out.splitlines()
        self.assertEqual(lines[0].split(), ["ID", "Name", "Gekoppelt", "Zuletzt", "gesehen"])
        by_id = {line.split()[0]: line for line in lines[1:]}
        self.assertEqual(set(by_id), {first["device_id"], second["device_id"]})
        self.assertIn("iPhone", by_id[first["device_id"]])
        self.assertTrue(by_id[first["device_id"]].rstrip().endswith("nie"))
        self.assertIn("2027-01-15 08:00 UTC", by_id[second["device_id"]])
        raw = (self.state_dir / "devices.json").read_text()
        hashes = re.findall(r"[0-9a-f]{64}", raw)
        self.assertEqual(len(hashes), 2)
        for secret in (*hashes, first["token"], second["token"]):
            self.assertNotIn(secret, out + err)
        self.assertIsNone(HEX_SECRET.search(out + err))
        self.assertNotIn("token", out.lower())

    def test_revoke_removes_the_device_and_its_token_stops_working(self):
        first, second = self.pair_device("iPhone"), self.pair_device("iPad")
        self.assertEqual(self.store().verify_token(first["token"]), first["device_id"])
        code, out, err = self.run_cli("revoke", first["device_id"])
        self.assertEqual(code, 0, err)
        self.assertIn(first["device_id"], out)
        self.assertIsNone(self.store().verify_token(first["token"]))
        self.assertEqual(self.store().verify_token(second["token"]), second["device_id"])
        self.assertEqual([d["id"] for d in self.store().list_devices()], [second["device_id"]])
        self.assertEqual(stat.S_IMODE(os.stat(self.state_dir / "devices.json").st_mode), 0o640)

    def test_revoke_of_unknown_or_malformed_ids_fails(self):
        self.pair_device("iPhone")
        for device_id in ("deadbeef", "XYZ", "../state", "0000000A", ""):
            with self.subTest(device_id=device_id):
                code, out, err = self.run_cli("revoke", device_id)
                self.assertEqual(code, 1, out)
                self.assertIn("FEHLER", err)
        self.assertEqual(len(self.store().list_devices()), 1)

    def test_revoke_without_state_dir_fails_cleanly(self):
        code, _, err = self.run_cli("revoke", "deadbeef")
        self.assertEqual(code, 1)
        self.assertIn("FEHLER", err)


class InitStateTests(PairingCliCase):
    def mode(self, path):
        return stat.S_IMODE(os.stat(path).st_mode)

    def test_creates_dir_0750_with_server_id_and_prints_the_gid(self):
        for umask in (0o022, 0o077):
            with self.subTest(umask=oct(umask)):
                self.setUp()
                old = os.umask(umask)
                try:
                    code, out, err = self.run_cli("init-state")
                finally:
                    os.umask(old)
                self.assertEqual(code, 0, err)
                self.assertEqual(self.mode(self.state_dir), 0o750)
                sid = (self.state_dir / "server_id").read_text().strip()
                self.assertRegex(sid, r"\A[0-9a-f]{8}\Z")
                self.assertEqual(self.mode(self.state_dir / "server_id"), 0o600)
                self.assertIn(f"KORE_STATE_GID={os.stat(self.state_dir).st_gid}", out)
                self.assertIn(sid, out)

    def test_is_idempotent_and_repairs_the_dir_mode(self):
        self.run_cli("init-state")
        sid = (self.state_dir / "server_id").read_text()
        os.chmod(self.state_dir, 0o700)
        os.chmod(self.state_dir / "server_id", 0o644)
        code, out, err = self.run_cli("init-state")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.mode(self.state_dir), 0o750)
        self.assertEqual(self.mode(self.state_dir / "server_id"), 0o600)
        self.assertEqual((self.state_dir / "server_id").read_text(), sid)

    def test_a_file_in_the_way_is_an_error(self):
        self.state_dir.write_text("x")
        code, _, err = self.run_cli("init-state")
        self.assertEqual(code, 1)
        self.assertIn("kein Ordner", err)

    def test_doctor_accepts_what_init_state_creates(self):
        self.run_cli("init-state")
        doctor = load_doctor()
        report = doctor.Report(stream=open(os.devnull, "w"))
        self.addCleanup(report.stream.close)
        doctor.check_state_dir(report, self.state_dir, {"KORE_STATE_GID": str(os.stat(self.state_dir).st_gid)})
        self.assertEqual(report.failures, 0)


class PairingContractUnitTests(unittest.TestCase):
    def setUp(self):
        self.doctor = load_doctor()

    def test_doctor_modes_equal_the_store_modes(self):
        self.assertEqual(self.doctor.STATE_DIR_MODE, pairing.STATE_DIR_MODE)
        store_modes = {name: pairing.FILE_MODES.get(name, pairing.PRIVATE_FILE_MODE)
                       for name in (pairing.DEVICES_FILE, pairing.PAIRING_FILE, pairing.SERVER_ID_FILE,
                                    pairing.LOCK_FILE)}
        self.assertEqual(self.doctor.STATE_FILE_MODES, store_modes)

    def test_link_building_encodes_everything_and_keeps_the_order(self):
        link = self.doctor.build_pair_link("http://10.0.0.10:8090", "http://10.0.0.10:8081",
                                           "ABCDEFGHIJKLMNOPQRST", "a1b2c3d4")
        self.assertEqual(link, "kore://pair?v=1&bridge=http%3A%2F%2F10.0.0.10%3A8090"
                               "&ingest=http%3A%2F%2F10.0.0.10%3A8081&code=ABCDEFGHIJKLMNOPQRST&sid=a1b2c3d4")

    def test_accepted_hosts_cover_the_private_and_vpn_ranges(self):
        accept = lambda url: self.doctor.validated_link_url(url, "x")  # noqa: E731
        self.assertEqual(accept("http://10.255.255.254:1"), "http://10.255.255.254:1")
        self.assertEqual(accept("http://172.16.0.1"), "http://172.16.0.1")
        self.assertEqual(accept("http://172.31.255.255:80"), "http://172.31.255.255:80")
        self.assertEqual(accept("https://192.168.0.1:443/"), "https://192.168.0.1:443")
        self.assertEqual(accept("https://100.127.255.255"), "https://100.127.255.255")
        self.assertEqual(accept("https://100.64.0.10:8443"), "https://100.64.0.10:8443")
        self.assertEqual(self.doctor.validated_link_url("http://10.0.0.10", "x", 8081), "http://10.0.0.10:8081")

    def test_plain_http_to_tailscale_addresses_is_refused_with_a_hint(self):
        for url in ("http://100.64.0.10", "http://100.127.255.255:8090", "HTTP://100.100.100.100:80"):
            with self.subTest(url=url):
                with self.assertRaises(self.doctor.PairingError) as caught:
                    self.doctor.validated_link_url(url, "x")
                self.assertIn("tailscale serve", str(caught.exception))
                self.assertIn("https://<name>.ts.net", str(caught.exception))

    def test_tailnet_https_hostnames_are_accepted(self):
        accept = lambda url, *a: self.doctor.validated_link_url(url, "x", *a)  # noqa: E731
        self.assertEqual(accept("https://my-host.tail1234.ts.net"), "https://my-host.tail1234.ts.net")
        self.assertEqual(accept("https://my-host.tail1234.ts.net:8443/"), "https://my-host.tail1234.ts.net:8443")
        self.assertEqual(accept("HTTPS://My-Host.Tail1234.TS.net", 443), "https://my-host.tail1234.ts.net:443")

    def test_other_hostnames_and_http_on_names_are_rejected(self):
        for url in ("http://my-host.tail1234.ts.net", "https://example.com", "https://ts.net",
                    "https://evil-ts.net", "https://my-host.tail1234.ts.net.example.com",
                    "https://my-host.tail1234.ts.net.", "https://-bad.tail1234.ts.net",
                    "https://user:pw@my-host.tail1234.ts.net", "https://my-host.tail1234.ts.net/path",
                    "https://my-host.tail1234.ts.net?x=1", "https://localhost", "https://8.8.8.8",
                    "https://127.0.0.1", "http://[::1]", "ftp://my-host.tail1234.ts.net", "my-host.tail1234.ts.net"):
            with self.subTest(url=url):
                with self.assertRaises(self.doctor.PairingError):
                    self.doctor.validated_link_url(url, "x")

    def test_state_dir_resolution_order(self):
        resolve = self.doctor.resolve_state_dir
        self.assertEqual(resolve("/srv/s", {"KORE_STATE_DIR": "/b"}, {}, {}), Path("/srv/s"))
        self.assertEqual(resolve(None, {"KORE_STATE_DIR": "/b"}, {"KORE_STATE_DIR": "/s"}, {}), Path("/b"))
        self.assertEqual(resolve(None, {}, {"KORE_STATE_DIR": "/s"}, {"KORE_STATE_DIR": "/e"}), Path("/s"))
        self.assertEqual(resolve(None, {}, {}, {"KORE_STATE_DIR": "/e"}), Path("/e"))
        self.assertEqual(resolve(None, {}, {"KORE_STATE_DIR": "./state"}, {}), self.doctor.REPO / "state")
        self.assertEqual(resolve(None, {}, {}, {}), self.doctor.REPO / "state")


if __name__ == "__main__":
    unittest.main()
