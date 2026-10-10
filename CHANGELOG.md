# Changelog

All notable changes to `kore-server`. Versions follow the Kore app's release it is tested with.

## 0.13.0

First public release.

### Added

- **Pairing.** `kore-doctor pair` creates a one-time code (20 characters, valid 10 minutes, consumed
  by the first attempt, 5 failed attempts per minute per address) and prints a `kore://pair` link and,
  with `qrencode` installed, a QR code. The app exchanges it at the new bridge route `POST /pair` for
  a per-device token that works for the bridge and the ingest API; the server stores only its SHA-256.
  `POST /unpair` removes a device from the app side. `kore-doctor devices` lists paired devices,
  `kore-doctor revoke <id>` cuts one off at once. The static tokens remain as the expert path.
  New capability `device_pairing_v1`; protocol in `contract/pairing-v1.md`.
- **`kore-doctor init-state`** creates the pairing state directory (`KORE_STATE_DIR`, default
  `./state`, mode 0750) and prints `KORE_STATE_GID`. The ingest container reads the device list
  read-only through that group (`group_add`); Compose refuses to start without `KORE_STATE_GID`.
  `kore-doctor` checks the directory, its permissions and the group.
- **VPN support.** The bridge accepts a bind address in `100.64.0.0/10` besides loopback and RFC 1918
  addresses. The README describes WireGuard on a router (the phone gets a home-network address, plain
  `http` works as at home) and Tailscale via `tailscale serve` with HTTPS (iOS blocks plain `http` to
  Tailscale addresses), including the note about certificate transparency logs.
- English `README.md` as the main document, German `README.de.md`; `CONTRIBUTING.md`, this changelog,
  issue and pull-request templates, `CODEOWNERS`.
- Supply chain: hash-locked dependencies (`--require-hashes`), base image pinned by digest, GitHub
  Actions pinned to commit SHAs, a gitleaks job over the full history and a pre-commit hook.

### Changed

- **MCP is loopback only.** `docker-compose.yml` publishes port 8000 on `127.0.0.1` and nowhere else;
  the Hermes agent runs on the same host. Profile example and docs use `http://127.0.0.1:8000/mcp/`.
- The bridge and docs speak neutrally ("your coach"); no internal references, no default paths
  that belong to one setup. `KORE_HERMES_HOME` (or `HOME`) must be known; the bridge names the missing
  key.
- Bridge and ingest API accept the static token **or** a device token.
- `hermes/kore-app-chat-soul.md` is neutral and adds a note that pairing is the bridge's business.
  `kore-doctor` reports an outdated Kore section in a profile's `SOUL.md`: replace it as the README
  describes.
- `coach-bridge/kore-coach-bridge.service` is now the hardened unit from the README (runs `bridge.py`
  from the repository checkout, `ProtectSystem=strict`, state directory in `ReadWritePaths`).
- `kore-doctor pair` also accepts `https://<name>.ts.net[:port]` (Tailscale serve). Plain `http` is only
  for private IPv4 addresses; to 100.64/10 (Tailscale) it is refused with a hint to use
  `tailscale serve` and `https://<name>.ts.net`, because iOS App Transport Security blocks it. Other
  host names, public addresses and loopback are still refused.

### Upgrading from 0.10

See "Coming from 0.10 or earlier" in the [README](README.md#updating): run `kore-doctor init-state`,
add `KORE_STATE_GID` to `.env`, update the `## Kore-App-Chat` section of the profile's `SOUL.md`.

## 0.10.0

Coach bridge as a bounded `hermes` child process with `stream-json` and a fixed tool list per chat
(`web,skills,memory`, plus `kore` in a health chat with an origin ticket); the prompt goes over
stdin; detached turns with server-sent events, idempotent replays and cancel; signed session
references, `/coach/forget` and 30-day session expiry; a capability contract and `kore-doctor`;
placeholder tokens refused at start; hardening of token comparison, size and socket limits,
container and CI.

## 0.9.0

Ingest API and MCP server (7 read tools, `propose_training_plan` with confirmation in the app and a
one-time origin ticket), origin check against DNS rebinding, Docker Compose, systemd units, example
Hermes profile.
