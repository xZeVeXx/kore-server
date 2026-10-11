# Changelog

All notable changes to `kore-server`. Versions follow the Kore app's release it is tested with.

## 0.13.0

First public release.

### Added

- **Pairing.** `kore-doctor pair` creates a one-time code (20 characters, valid 10 minutes, used up by
  the first successful pairing and dead after 5 failed attempts with a well-formed code; at most 5
  failed attempts per minute per address) and prints a `kore://pair` link and,
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
- **Coach name.** Optional `KORE_COACH_NAME` in `bridge.env` (1 to 24 characters: letters, digits,
  space, hyphen, apostrophe, period). A valid name appears as `"coach_name"` in `/health` and
  `/capabilities`; an invalid one is ignored with one startup warning, the bridge still starts. The
  capability list is unchanged: the field's presence is the signal.
- README: choosing a new or an existing Hermes profile, including the consequences of an existing one
  (health data in its general memory, Kore section in its `SOUL.md`, the `kore` MCP server in it, an
  external memory provider receives Kore chats, deleting in Kore does not reach them); model providers
  that are Hermes plugins are enabled per profile without copying credentials; `KORE_HERMES_CHILD_PATH`
  documented precisely (it replaces the default `PATH`); install `qrencode` for the QR code in
  `kore-doctor pair`.
- English `README.md` as the main document, German `README.de.md`; `CONTRIBUTING.md`, this changelog,
  issue and pull-request templates, `CODEOWNERS`.
- Supply chain: hash-locked dependencies (`--require-hashes`), base image pinned by digest, GitHub
  Actions pinned to commit SHAs, a gitleaks job over the full history and a pre-commit hook.

### Changed

- **Tailscale target made consistent.** The recommended setup keeps the services on the LAN address
  and `tailscale serve` forwards to `http://<LAN address>:8081` / `:8090`; loopback plus `serve` to
  `127.0.0.1` is described as the Tailscale-only variant. One rule: `serve` forwards to the address the
  service listens on.
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
- **Health chats have no web search.** The tool list of a health chat is now `skills,memory,kore`
  (before: `web,skills,memory,kore`); health context without an origin ticket, as when adjusting
  today's workout, gets `skills,memory` instead of the general list. The general chat keeps
  `web,skills,memory`. `/health` reports it under `app_chat_toolsets`.
- **Hermes runs share one quota.** At most 2 run at once whichever route they come by; the
  synchronous `POST /coach` and `POST /coach/adjust-today` now answer `429` with `code: "busy"` when
  both places are taken, as the background turns did before. A turn keeps its place until Hermes has
  really ended.
- `coach-bridge/fixtures/` and the fixture notes no longer carry the model name or the memory
  provider of the machine they were recorded on (placeholders `<modell>` and `<provider>`).

### Security

Hardening from a security review of bridge, server and deployment before the release.

Coach bridge:

- **Fixed first line in every prompt.** Hermes reads a file path at the very start of a message
  (`/…`, `~/…`, `file://…`, `C:/…`) as an attachment, and an image file would reach the vision model
  past the tool list. Every prompt now begins with `[KORE-APP-NACHRICHT]`.
- **No `web` in the health scope.** Health context can no longer be sent out by a web tool; the
  adjustment of today's workout, which carries health context, lost it as well. `kore-doctor` fails
  when the bridge reports `web` for the health scope.
- **`skills.inline_shell` must be off.** With it on, Hermes runs shell commands from a skill's
  `SKILL.md`. The bridge does not start unless the profile's `config.yaml` has it missing or exactly
  `false`; `kore-doctor` checks it; the example profile sets it to `false`.
- **"Only reduce" is enforced.** The adjusted workout of the day is compared with the one the app sent;
  more sets, weight, repetitions or duration, new or renamed exercises or another session id make the
  bridge answer `502` with a fixed sentence and return nothing of the reply. It used to be only an
  instruction in the prompt. The body carries `"code": "escalation"` next to the sentence, so the app
  can tell this rejection from other 502s without matching text.
- **One quota for every Hermes run** (see Changed) instead of a limit that covered only background
  turns.
- **Exactly one `Authorization` header** counts on the bridge, as on the ingest API: a request with two
  gets `401` (also on `POST /unpair`) before any of the body is read.
- **Connection cap and read deadline.** At most 64 connections at once, 20 s from the start of a
  connection for headers and body; a slow sender can no longer hold a thread open. The unit gets
  `TasksMax=512`, `MemoryMax=2G` and `LimitNOFILE=4096`.
- **Pairing codes.** A request without a well-formed code no longer uses up the open code; the code is
  used up by the first success and dead after 5 failed attempts with it. The per-address limit checks
  and counts in one step, so parallel requests cannot exceed it.
- **Bridge token of at least 32 characters** (the token also keys the session references).
- **Forget and expiry check the source** of every listed session (`kore-<scope>`) before deleting it.
- **Each Hermes run gets a fresh, empty working directory** instead of the home directory, so
  `AGENTS.md`, `CLAUDE.md` and similar context files there are not loaded into the prompt.

Server:

- **Authentication before the body (ingest).** The bearer token is checked in front of the web
  framework, before any of the body is read. A request without a valid token used to make the server
  read and parse up to 16 MiB first; it now gets `401` with `Connection: close` at once, and anything
  unauthenticated may send at most 4 KiB. Exactly one `Authorization` header counts.
- **No `/docs`, `/redoc` or `/openapi.json`** on the ingest API.
- **MCP token compared in constant time** (SHA-256 digests of equal length, no dictionary lookup of the
  token).
- **Ingest and MCP tokens of at least 32 characters.**
- **Listen addresses are checked.** Ingest and MCP apply the bridge's rule (concrete loopback, RFC 1918
  or `100.64.0.0/10`; no wildcard, no host name) before opening a socket. In Docker the check applies to
  the address Compose publishes the port on (`KORE_PUBLISHED_BIND`).
- **Separate container environments.** No `env_file`: ingest holds neither the MCP token nor the
  origin secret, the MCP server holds no ingest token.
- **Container and unit limits.** Read-only root file system, `noexec` tmpfs for `/tmp`, process and
  memory limits and a health check for the ingest container; ingest and MCP units run sandboxed
  (`ProtectSystem=strict`, `PrivateTmp`, restricted address families and namespaces, empty capability
  set) with `MemoryMax`, `TasksMax` and `LimitNOFILE`.
- Starlette 1.3.1 (security update for the limits of form parsing), pydantic 2.13.5; lockfiles
  regenerated.

Documented residual risks, now in the README: the profile's memory as a two-step path from health
chats to web search, the Linux user as the trust boundary, plain HTTP also for pairing, Docker before
28 and loopback ports, the global (not per-address) connection limit, no brake on guessing bearer
tokens, and the startup-only check of `skills.inline_shell`.

### Breaking for self-hosters

- **Tokens under 32 characters stop the services.** `KORE_BRIDGE_TOKEN`, `KORE_INGEST_TOKEN` and
  `KORE_MCP_TOKEN` need at least 32 characters (`openssl rand -hex 24` gives 48). Replace a short one,
  restart, and update the other side (Kore on the expert path, the profile's `.env` for the MCP token).
- **`skills.inline_shell` on stops the bridge.** Set it to `false` or remove it.
- **Extra `.env` keys must be mapped in `docker-compose.yml`.** Compose reads `.env` only to fill in
  values; each service gets exactly the keys listed under its `environment:`. A key you added yourself
  reaches a container only when you add it there.
- **Custom units must start `python ingest_api.py`** (and `python mcp_server.py`), not `uvicorn`
  directly; the scripts check the listen address. The ingest unit and the Dockerfile do so already.
  `KORE_INGEST_HOST`, `KORE_MCP_HOST` and, in Compose only, `KORE_PUBLISHED_BIND` are new; do not put
  `KORE_PUBLISHED_BIND` into `.env`.
- **A listen address outside loopback, RFC 1918 and `100.64.0.0/10` stops the services**, `0.0.0.0`
  included: with `KORE_BIND_ADDRESS=0.0.0.0` the ingest container now restarts in a loop with a message
  in `docker compose logs ingest`.
- **Two `Authorization` headers are rejected** by the ingest API (before, the first one counted).
- **The health chat has no web search** any more; asking the coach in a health chat to search the web
  no longer works. Use the general chat for that.
- **The bridge unit changed** (`TasksMax`, `MemoryMax`, `LimitNOFILE`); copy the unit from the README
  again.

### Upgrading from 0.10

See "Coming from 0.10 or earlier" in the [README](README.md#updating): run `kore-doctor init-state`,
add `KORE_STATE_GID` to `.env`, update the `## Kore-App-Chat` section of the profile's `SOUL.md`, and
check "Breaking for self-hosters" above.

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
