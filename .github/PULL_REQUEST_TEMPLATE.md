## What and why

<!-- One or two sentences. Link the issue if there is one. -->

## Checklist

- [ ] The three test suites pass (see CONTRIBUTING.md): `pytest coach-bridge -q`, `pytest scripts -q`, `pytest server/tests server/integration_tests -q`
- [ ] New behavior has a test; a bug fix has a test that failed before
- [ ] No new dependency in `coach-bridge/` or `scripts/` (standard library only); if `server/requirements*.txt` changed, the lockfiles are regenerated
- [ ] No tokens, one-time codes, health data, real IP addresses, host names or personal paths in code, tests, fixtures, docs or the commit message (examples: `10.0.0.10`, `100.64.0.10`)
- [ ] Docs updated if behavior, commands or environment variables changed (`README.md` and `README.de.md`), and a line in `CHANGELOG.md`
- [ ] Nothing that listens on `0.0.0.0` on the host, forwards a port or weakens the token, origin or pairing checks
