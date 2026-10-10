# Contributing

Thanks for looking. `kore-server` is a small, security-sensitive project: it holds a person's health
summary and starts an AI agent on their machine. Changes are welcome when they keep it small and
keep the guarantees in the README's [Security](README.md#security) section.

Issues and pull requests may be in English or German.

## Never post health data or secrets

Not in an issue, a pull request, a log excerpt, a screenshot or a test fixture: **no tokens, no
one-time codes, no `kore://pair` links, no real health data, no real IP addresses or host names.**
`kore-doctor` prints no secrets, so its output is safe to paste (read it first anyway). Use
placeholders such as `<token>` and the example addresses `10.0.0.10` and `100.64.0.10`.

Found a security problem? Do not open an issue. Use **Security → Report a vulnerability**, see
[SECURITY.md](SECURITY.md).

## Running the tests

You need Python 3.12. No Docker is needed for the tests.

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install --require-hashes -r server/requirements-dev.lock
pytest coach-bridge -q
pytest scripts -q
pytest server/tests server/integration_tests -q
```

Run them as three separate commands, exactly as in [`.github/workflows/ci.yml`](.github/workflows/ci.yml):
the server tests reload their modules with their own environment and do not coexist with the other
suites in a single `pytest` process. All three must pass.

## Changing dependencies

`server/requirements.txt` (runtime) and `server/requirements-dev.txt` (plus pytest) are the
hand-maintained inputs with pinned versions. Installs come only from the lockfiles
`server/requirements.lock` and `server/requirements-dev.lock`, which pin every package with its
hashes. After changing an input (or merging a Dependabot update), regenerate both:

```bash
cd server
uv pip compile requirements.txt     --generate-hashes --universal --python-version 3.12 -o requirements.lock
uv pip compile requirements-dev.txt --generate-hashes --universal --python-version 3.12 -o requirements-dev.lock
```

(`pip-compile --generate-hashes` from pip-tools gives the same; `uv` is a development tool, not a
runtime dependency.) A test fails when an input and its lockfile drift apart. The coach bridge
(`coach-bridge/`) and `scripts/kore-doctor` use only the Python standard library; please keep it that
way.

## Secret scanning

CI runs gitleaks over the whole history. Run it before every commit with pre-commit:

```bash
pip install pre-commit
pre-commit install
```

The hook scans the staged changes with [`.gitleaks.toml`](.gitleaks.toml). That file extends the
default rules and allows only the documented `change-me…` placeholders in example files, the README
and tests. Do not widen the allowlist to silence a finding; remove the secret instead (and rotate it
if it was real).

## Pull requests

- Keep a change focused, and explain *why* in the description.
- Add a test with a behavior change; for a bug fix, a test that fails without the fix.
- Update `README.md` **and** `README.de.md` when commands, environment variables or behavior
  change, and add a line to [`CHANGELOG.md`](CHANGELOG.md).
- Keep the guarantees: nothing listens on `0.0.0.0` on the host, no port forwarding advice, tokens
  compared in constant time, no secrets or health values in logs, the prompt on stdin, the tool
  list fixed by the bridge.
- Fixtures under `coach-bridge/fixtures/` come from real Hermes runs with synthetic content. If you
  record new ones, use made-up questions and data, and mark hand-built files as synthetic.
- Examples and tests use only `10.0.0.x`, `192.0.2.x` and `100.64.0.x` addresses and no personal names or
  paths.

Commit messages are short and in the imperative (`fix(bridge): …`, `docs: …`).
