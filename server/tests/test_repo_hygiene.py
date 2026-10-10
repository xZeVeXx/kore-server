"""Was ein öffentliches Repo nie enthalten oder erlauben darf (0.10).

Geheimnisse und Backups bleiben aus Git und aus dem Docker-Build-Kontext, Container laufen ohne
Root-Rechte und ohne Capabilities, die CI hat nur Leserecht.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import unittest
from pathlib import Path


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
        # Ingest follows KORE_BIND_ADDRESS (loopback by default); MCP is always loopback-only.
        self.assertEqual(sorted(ports), [
            '- "${KORE_BIND_ADDRESS:-127.0.0.1}:8081:8081"',
            '- "127.0.0.1:8000:8000"',
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
