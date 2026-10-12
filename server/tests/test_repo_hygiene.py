"""Was ein öffentliches Repo nie enthalten oder erlauben darf (0.10).

Geheimnisse und Backups bleiben aus Git und aus dem Docker-Build-Kontext, Container laufen ohne
Root-Rechte und ohne Capabilities, die CI hat nur Leserecht.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(REPO_ROOT), *args], capture_output=True, text=True)


@unittest.skipUnless(
    shutil.which("git") and (REPO_ROOT / ".git").exists(), "braucht einen Git-Checkout"
)
class GitignoreTests(unittest.TestCase):
    def test_secrets_backups_and_keys_are_ignored(self) -> None:
        for path in (".env", ".env.local", "coach-bridge/bridge.env", "server/.env.prod",
                     "coach-bridge/bridge.env.bak-20260925", "SOUL.md.bak", "server/kore.db",
                     "server/kore.db-wal", "deploy/tls.key", "cert.pem"):
            with self.subTest(path=path):
                self.assertEqual(_git("check-ignore", "-q", path).returncode, 0)

    def test_the_example_files_stay_tracked(self) -> None:
        for path in (".env.example", "coach-bridge/bridge.env.example"):
            with self.subTest(path=path):
                self.assertEqual(_git("check-ignore", "-q", path).returncode, 1)

    def test_no_tracked_file_is_ignored(self) -> None:
        self.assertEqual(_git("ls-files", "-ci", "--exclude-standard").stdout, "")


class ContainerTests(unittest.TestCase):
    def test_dockerignore_keeps_secrets_out_of_the_build_context(self) -> None:
        entries = (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8").split()
        for pattern in ("**/*.env", "**/.env", "**/.env.*", "**/*.db", "**/*.bak*", ".git"):
            with self.subTest(pattern=pattern):
                self.assertIn(pattern, entries)

    def test_image_runs_as_unprivileged_user_without_baked_secrets(self) -> None:
        dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
        users = [line.split()[1] for line in dockerfile.splitlines() if line.startswith("USER ")]
        self.assertTrue(users and users[-1] not in ("root", "0"))
        for forbidden in ("TOKEN", "SECRET", ".env"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, dockerfile)

    def test_compose_drops_privileges_and_publishes_on_loopback_by_default(self) -> None:
        compose = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        self.assertNotIn("privileged", compose)
        self.assertNotIn("network_mode", compose)
        self.assertEqual(compose.count("no-new-privileges:true"), 2)
        self.assertEqual(compose.count("cap_drop:\n      - ALL"), 2)
        ports = [line.strip() for line in compose.splitlines() if line.strip().startswith('- "')]
        self.assertEqual(len(ports), 2)
        # Ingest follows KORE_BIND_ADDRESS (loopback by default) and KORE_INGEST_PORT (8081);
        # MCP is always loopback-only, only its host port follows KORE_MCP_PORT (8000).
        self.assertEqual(sorted(ports), [
            '- "${KORE_BIND_ADDRESS:-127.0.0.1}:${KORE_INGEST_PORT:-8081}:8081"',
            '- "127.0.0.1:${KORE_MCP_PORT:-8000}:8000"',
        ])

    def test_ingest_reads_the_pairing_state_through_a_required_group_only(self) -> None:
        compose = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        ingest, mcp = compose.split("\n  mcp:\n")
        # Required (no default that would silently grant nothing), read-only mount, ingest only.
        self.assertIn('group_add: ["${KORE_STATE_GID:?', ingest)
        self.assertIn(":/state:ro", ingest)
        self.assertNotIn("group_add", mcp)
        self.assertNotIn("/state", mcp)
        self.assertEqual(compose.count("group_add"), 1)
        example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
        self.assertIn("\nKORE_STATE_GID=", example)
        self.assertIn("KORE_STATE_DIR", example)


def _compose() -> dict:
    return yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))["services"]


_INTERPOLATION = re.compile(r"\$\{(?P<name>[A-Z_]+)(?::(?P<op>[?-])(?P<rest>[^}]*))?\}")


def _resolve(value: str, fixtures: dict[str, str]) -> str:
    """Compose's `${VAR}`, `${VAR:-default}` and `${VAR:?message}` against a stand-in `.env`."""
    def substitute(match: re.Match) -> str:
        name, op, rest = match.group("name", "op", "rest")
        found = fixtures.get(name)
        if found:
            return found
        if op == "-":
            return rest
        raise AssertionError(f"compose requires {name}, which the stand-in .env does not set")
    return _INTERPOLATION.sub(substitute, str(value))


class ContainerHardeningTests(unittest.TestCase):
    """0.13 S11b: resource limits, read-only root, one environment per service."""

    def test_both_services_are_read_only_with_a_small_tmpfs_and_hard_limits(self) -> None:
        for name, service in _compose().items():
            with self.subTest(service=name):
                self.assertIs(service.get("read_only"), True)
                tmpfs = service.get("tmpfs")
                self.assertEqual(len(tmpfs), 1)
                self.assertRegex(tmpfs[0], r"^/tmp:.*\bnoexec\b.*\bnosuid\b.*\bsize=\d+m\b")
                self.assertRegex(str(service.get("mem_limit")), r"^\d+m$")
                self.assertIsInstance(service.get("pids_limit"), int)
                self.assertLessEqual(service["pids_limit"], 256)

    def test_the_only_writable_places_are_the_database_volume_and_tmp(self) -> None:
        for name, service in _compose().items():
            with self.subTest(service=name):
                writable = [volume for volume in service["volumes"] if not volume.endswith(":ro")]
                self.assertEqual(writable, ["kore-db:/data"])

    def test_ingest_has_a_healthcheck_against_its_open_health_route(self) -> None:
        check = _compose()["ingest"]["healthcheck"]
        self.assertEqual(check["test"][:3], ["CMD", "python", "-c"])
        self.assertIn("http://127.0.0.1:8081/health", check["test"][3])
        for key in ("interval", "timeout", "retries"):
            self.assertIn(key, check)

    def test_the_mcp_container_turns_the_fastmcp_update_check_off(self) -> None:
        # Quoted on purpose: a bare `off` is the boolean false in YAML 1.1.
        self.assertEqual(_compose()["mcp"]["environment"]["FASTMCP_CHECK_FOR_UPDATES"], "off")

    def test_no_service_loads_the_whole_env_file(self) -> None:
        compose = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        self.assertNotIn("env_file", compose)

    def test_each_service_holds_only_its_own_secrets(self) -> None:
        services = _compose()
        ingest, mcp = services["ingest"]["environment"], services["mcp"]["environment"]
        self.assertIn("KORE_INGEST_TOKEN", ingest)
        self.assertIn("KORE_MCP_TOKEN", mcp)
        self.assertIn("KORE_PLAN_ORIGIN_SECRET", mcp)
        # Plan tickets are verified in the MCP server only; ingest just reads the principal.
        for key in ("KORE_MCP_TOKEN", "KORE_PLAN_ORIGIN_SECRET", "KORE_MCP_CLIENT_ID"):
            with self.subTest(ingest_must_not_hold=key):
                self.assertNotIn(key, ingest)
        for key in ("KORE_INGEST_TOKEN", "KORE_STATE_DIR", "KORE_BIND_ADDRESS"):
            with self.subTest(mcp_must_not_hold=key):
                self.assertNotIn(key, mcp)

    def test_the_declared_environment_is_enough_to_start_each_service(self) -> None:
        # Resolve the compose environment against a stand-in .env and import the real module
        # with exactly that, nothing else: a key a service needs but lost in the split fails here.
        fixtures = {
            "KORE_INGEST_TOKEN": "i" * 48, "KORE_MCP_TOKEN": "m" * 48,
            "KORE_MCP_CLIENT_ID": "hermes", "KORE_PLAN_ORIGIN_SECRET": "o" * 48,
            "KORE_INSTANCE_ID": "kore", "KORE_PLAN_ORIGIN_PRINCIPAL": "kore",
        }
        with tempfile.TemporaryDirectory() as temp:
            for name, module in (("ingest", "ingest_api"), ("mcp", "mcp_server")):
                with self.subTest(service=name):
                    env = {key: _resolve(value, fixtures)
                           for key, value in _compose()[name]["environment"].items()}
                    env["KORE_DB_PATH"] = str(Path(temp) / f"{name}.db")
                    env.pop("KORE_STATE_DIR", None)
                    env["PYTHONPATH"] = str(REPO_ROOT / "server")
                    completed = subprocess.run(
                        [sys.executable, "-c", f"import {module}"], cwd=temp, env=env,
                        capture_output=True, text=True, timeout=60, check=False,
                    )
                    self.assertEqual(completed.returncode, 0, completed.stderr[-600:])

    def test_the_two_optional_defaults_in_compose_match_the_code(self) -> None:
        sys.path.insert(0, str(REPO_ROOT / "server"))
        try:
            from origin_ticket import policy_from_env
        finally:
            sys.path.remove(str(REPO_ROOT / "server"))
        with mock.patch.dict(os.environ):
            for key in [key for key in os.environ if key.startswith("KORE_PLAN_ORIGIN")]:
                del os.environ[key]
            os.environ["KORE_PLAN_ORIGIN_PRINCIPAL"] = "kore"
            policy = policy_from_env()
        for name, service in _compose().items():
            with self.subTest(service=name):
                env = service["environment"]
                self.assertEqual(
                    _resolve(env["KORE_PLAN_ORIGIN_MAX_TTL_SECONDS"], {}), str(policy.max_ttl_seconds))
                self.assertEqual(
                    _resolve(env["KORE_PLAN_ORIGIN_CLOCK_SKEW_SECONDS"], {}), str(policy.clock_skew_seconds))

    def test_the_container_listens_wide_but_must_state_where_compose_publishes_it(self) -> None:
        services = _compose()
        ingest, mcp = services["ingest"], services["mcp"]
        self.assertEqual(ingest["environment"]["KORE_INGEST_HOST"], "0.0.0.0")
        self.assertEqual(mcp["environment"]["KORE_MCP_HOST"], "0.0.0.0")
        # The same expression that decides the published host address, so the two cannot diverge.
        self.assertEqual(ingest["environment"]["KORE_PUBLISHED_BIND"], "${KORE_BIND_ADDRESS:-127.0.0.1}")
        self.assertTrue(ingest["ports"][0].startswith("${KORE_BIND_ADDRESS:-127.0.0.1}:"))
        self.assertTrue(ingest["ports"][0].endswith(":8081"))
        self.assertEqual(mcp["environment"]["KORE_PUBLISHED_BIND"], "127.0.0.1")
        self.assertTrue(mcp["ports"][0].startswith("127.0.0.1:"))
        self.assertTrue(mcp["ports"][0].endswith(":8000"))

    def test_host_ports_follow_the_env_and_the_container_side_stays_fixed(self) -> None:
        # 0.14 S1: a second stack on the same host needs no compose override any more.
        services = _compose()
        ingest, mcp = services["ingest"], services["mcp"]
        moved = {"KORE_INGEST_PORT": "18081", "KORE_MCP_PORT": "18000"}
        self.assertEqual(_resolve(ingest["ports"][0], {}), "127.0.0.1:8081:8081")
        self.assertEqual(_resolve(mcp["ports"][0], {}), "127.0.0.1:8000:8000")
        self.assertEqual(_resolve(ingest["ports"][0], moved), "127.0.0.1:18081:8081")
        self.assertEqual(_resolve(mcp["ports"][0], moved), "127.0.0.1:18000:8000")
        # The LAN address moves the ingest only; the MCP stays on loopback whatever .env says.
        lan = {"KORE_BIND_ADDRESS": "10.0.0.10", **moved}
        self.assertEqual(_resolve(ingest["ports"][0], lan), "10.0.0.10:18081:8081")
        self.assertEqual(_resolve(mcp["ports"][0], lan), "127.0.0.1:18000:8000")
        # Inside the containers nothing moves: the processes listen on 8081 and 8000 as before.
        self.assertEqual(mcp["environment"]["KORE_MCP_PORT"], "8000")
        self.assertNotIn("KORE_INGEST_PORT", ingest["environment"])
        self.assertIn("http://127.0.0.1:8081/health", ingest["healthcheck"]["test"][3])

    def test_the_env_example_documents_both_host_ports(self) -> None:
        example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
        self.assertRegex(example, r"(?m)^#?\s*KORE_INGEST_PORT=8081$")
        self.assertRegex(example, r"(?m)^#?\s*KORE_MCP_PORT=8000$")

    def test_the_image_starts_through_the_bind_guard_not_a_bare_wildcard_uvicorn(self) -> None:
        dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
        cmd = [line for line in dockerfile.splitlines() if line.startswith("CMD ")]
        self.assertEqual(cmd, ['CMD ["python", "ingest_api.py"]'])
        self.assertNotIn("0.0.0.0", dockerfile)
        self.assertEqual(_compose()["ingest"]["command"], ["python", "ingest_api.py"])


def _readme_headings_and_links(name: str) -> tuple[set[str], list[str]]:
    """GitHub-style heading anchors of a README and its in-page links (code fences left out)."""
    slugs, links, in_fence = set(), [], False
    for line in (REPO_ROOT / name).read_text(encoding="utf-8").splitlines():
        if line.startswith("```"):
            in_fence = not in_fence
        elif not in_fence:
            heading = re.match(r"#{1,6}\s+(.*\S)", line)
            if heading:
                slugs.add(re.sub(r"[^\w\- ]", "", heading.group(1).lower()).replace(" ", "-"))
            links += re.findall(r"\]\(#([^)\s]+)\)", line)
    return slugs, links


class ReadmeAnchorTests(unittest.TestCase):
    def test_every_in_page_link_has_a_heading(self) -> None:
        for name in ("README.md", "README.de.md"):
            slugs, links = _readme_headings_and_links(name)
            self.assertTrue(links)
            for link in links:
                with self.subTest(readme=name, link=link):
                    self.assertIn(link, slugs)

    def test_the_upgrade_anchors_the_app_links_to_exist(self) -> None:
        # Kore opens README.md#upgrading and README.de.md#aktualisieren (0.14 A2); do not rename them.
        self.assertIn("upgrading", _readme_headings_and_links("README.md")[0])
        self.assertIn("aktualisieren", _readme_headings_and_links("README.de.md")[0])


class WorkflowTests(unittest.TestCase):
    def test_ci_has_read_only_token_and_no_privileged_trigger(self) -> None:
        workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertIn("permissions:\n  contents: read", workflow)
        self.assertNotIn("pull_request_target", workflow)
        self.assertNotIn("secrets.", workflow)
        self.assertEqual(workflow.count("persist-credentials: false"),
                         workflow.count("actions/checkout@"))

    def test_every_action_is_pinned_to_a_full_commit_sha(self) -> None:
        workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        uses = re.findall(r"^\s*-?\s*uses:\s*(\S+)(.*)$", workflow, flags=re.MULTILINE)
        self.assertTrue(uses)
        for ref, rest in uses:
            with self.subTest(ref=ref):
                self.assertRegex(ref, r"@[0-9a-f]{40}$")
                self.assertRegex(rest, r"#\s*v\d+(\.\d+)*")

    def test_dependencies_install_only_from_hashed_lockfiles(self) -> None:
        workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        installs = [l for l in workflow.splitlines() if "pip install" in l]
        self.assertTrue(installs)
        for line in installs:
            with self.subTest(line=line):
                self.assertIn("--require-hashes -r server/requirements-dev.lock", line)

    def test_gitleaks_scans_the_full_history_with_a_verified_binary(self) -> None:
        workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        job = workflow.split("\n  gitleaks:\n")[1]
        self.assertIn("fetch-depth: 0", job)
        self.assertRegex(job, r'GITLEAKS_SHA256: "[0-9a-f]{64}"')
        self.assertIn("sha256sum --check", job)
        self.assertIn("--config .gitleaks.toml", job)


class SupplyChainTests(unittest.TestCase):
    def test_dockerfile_pins_the_base_image_digest_and_installs_with_hashes(self) -> None:
        dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertRegex(dockerfile, r"(?m)^FROM python:3\.12-slim@sha256:[0-9a-f]{64}$")
        self.assertIn("pip install --no-cache-dir --require-hashes -r requirements.lock", dockerfile)
        self.assertIn("COPY server/requirements.lock", dockerfile)

    def test_the_service_user_can_read_code_from_an_owner_only_checkout(self) -> None:
        # A checkout made under `umask 077` has 0600 files; COPY keeps the mode, and the ingest (uid 10001)
        # then fails on import with PermissionError. The image makes the copied code readable first.
        dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
        copy = dockerfile.index("COPY server/ ./")
        readable = dockerfile.index("RUN chmod -R a+rX /app")
        user = dockerfile.index("USER kore")
        self.assertLess(copy, readable)
        self.assertLess(readable, user)

    def test_lockfiles_carry_hashes_and_cover_their_inputs(self) -> None:
        for source, lock in (("requirements.txt", "requirements.lock"),
                             ("requirements-dev.txt", "requirements-dev.lock")):
            with self.subTest(lock=lock):
                locked = (REPO_ROOT / "server" / lock).read_text(encoding="utf-8")
                # Every pinned requirement in the input must appear with the same version.
                for line in (REPO_ROOT / "server" / source).read_text(encoding="utf-8").splitlines():
                    match = re.fullmatch(r"([A-Za-z0-9_.-]+)(?:\[[^\]]*\])?==([^\s;]+)", line.strip())
                    if match:
                        name, version = match.groups()
                        self.assertRegex(locked, rf"(?mi)^{re.escape(name)}=={re.escape(version)}\b")
                # Every package in the lock has at least one hash.
                for block in re.split(r"(?m)^(?=[A-Za-z0-9])", locked):
                    if re.match(r"[A-Za-z0-9_.-]+==", block):
                        self.assertIn("--hash=sha256:", block.splitlines()[0] + block)

    def test_gitleaks_config_extends_the_defaults_and_comments_each_allowlist(self) -> None:
        config = (REPO_ROOT / ".gitleaks.toml").read_text(encoding="utf-8")
        self.assertIn("useDefault = true", config)
        self.assertEqual(config.count("[[allowlists]]"), 1)
        self.assertIn("change-me", config)
        hook = (REPO_ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")
        self.assertRegex(hook, r"rev: [0-9a-f]{40} # v\d+\.\d+\.\d+")
        self.assertIn("id: gitleaks", hook)

    def test_dependabot_covers_pip_actions_and_docker(self) -> None:
        config = (REPO_ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")
        for ecosystem in ("pip", "github-actions", "docker"):
            with self.subTest(ecosystem=ecosystem):
                self.assertIn(f"package-ecosystem: {ecosystem}", config)


if __name__ == "__main__":
    unittest.main()
