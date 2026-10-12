# Changelog

All notable changes to `kore-server`. Versions follow the Kore app's release it is tested with.

## Unreleased

### Fixed

- **Snapshots with Apple's recovery HRV were rejected (422).** Since app 0.10, Kore can carry the day
  on Apple's own recovery HRV (iOS 27). The app then sends `hrv_kind`/`hrv_method_effective`
  `appleRMSSD` and `hrv_source` `appleRecoveryRMSSD` with the neutral `hrv_ms` value. The ingest did
  not know these values and rejected the whole snapshot, so the coach saw no data at all. They are
  now accepted, never mixed with `hrv_rmssd_ms`/`hrv_sdnn_ms` or the rMSSD/SDNN sources, and the
  `get_recovery_today` tool tells the coach that this is a scale of its own.
- **Bridge start with Telegram topics in the profile.** Hermes writes Telegram topic prompts with
  single-quoted numeric keys (`'1': ...`). The strict `skills.inline_shell` check rejected every
  quoted key, so the bridge refused to start for such profiles. Single-quoted keys made only of
  `A-Z a-z 0-9 . _ ~ + -` are now accepted when they are indented under a block other than `skills:`.
  Quoted keys at the top level, inside `skills:`, with double quotes, in list items or naming
  `inline_shell` stay rejected. `kore-doctor` judges the same way.

### Security

- **Two ways past the `skills.inline_shell` lock closed.** Hermes enables inline shell for any
  non-empty value, and two texts that read as `false` were not: a deeper continuation line right
  after `inline_shell: false` (YAML folds it into `"false yes"`) and Unicode whitespace such as
  NBSP after `false`. Only someone who can already write the Hermes profile's `config.yaml` could
  use them. The bridge and `kore-doctor` now treat only space and tab as YAML whitespace and reject
  a deeper line directly after the `inline_shell` line.

### Added

- **Ports for a second stack.** `KORE_INGEST_PORT` (default 8081) and `KORE_MCP_PORT` (default 8000)
  in `.env` move the host ports Docker Compose publishes; inside the containers nothing changes, and
  the MCP stays on `127.0.0.1`. `kore-doctor` and `kore-doctor pair` read both values from the
  server `.env`, so checks and pairing links follow the moved ports.
- **`KORE_HERMES_DATA_DIR`** in `bridge.env` for Hermes installations whose data directory is not
  `<HOME>/.hermes`, such as the Hermes Docker image (`/opt/data`). The bridge passes it to Hermes as
  `HERMES_HOME` and looks for the profile in `<dir>/profiles/<name>` (`<dir>/config.yaml` for
  `default`); `kore-doctor` looks in the same place. The value must be an absolute path without `..`,
  otherwise the bridge does not start. The Hermes environment stays an allowlist: `HERMES_HOME` is the
  only addition and never comes from the service's own environment.
- **`kore-doctor`: `KORE_MCP_URL`** (optional, server `.env`, ignored by Compose). If set, for an MCP
  that only lives in the Compose network (e.g. `http://mcp:8000/mcp/`), the doctor skips its check on
  `127.0.0.1` with a `[HINWEIS]` line instead of failing. `hermes mcp test kore` still checks the connection.
- **`kore-doctor`: Hermes without the `mcp` extra.** If `hermes mcp test kore` fails with
  `No module named 'mcp'` (or an equivalent message), the doctor names the remedy,
  `hermes pm install --extra mcp`. The cause is on the Hermes side. The test now has a 30 second
  limit and only the first 64 KiB of its output are examined.
- **Steps, weight and nutrition for the coach (snapshot v2).** The snapshot takes three optional
  daily lists of up to 31 days: `steps` (daily total), `body` (weight in kg, body fat in percent, the
  day's last value) and `nutrition` (kcal, protein, carbohydrates, fat; daily totals). Each list is
  strictly bounded, one entry per day. A list in the snapshot replaces the stored one, an empty list
  clears it, a missing list keeps it, so the 0.13 app never wipes them. New read-only MCP tools
  `get_activity_body_trend(days)` and `get_nutrition_trend(days)` (1 to 30 days, newest last);
  `get_coach_briefing` adds the last 7 days as `activity_body_7d` and `nutrition_7d`. Contract:
  `contract/snapshot-v2.md`.
- **`GET /capabilities` on the ingest.** With the ingest or a device token it answers
  `{"snapshot_capabilities": ["activity_body_nutrition_v1", "apple_hrv_v1"]}`. The app 0.14 reads it
  before each sync and sends the new lists and Apple's HRV labels only to an ingest that names them,
  so it keeps syncing with a v0.13.0 ingest (which answers 404 there). `/health` stays `{"ok": true}`.
- **Docker/Unraid guide.** New README section "Hermes in a Docker container (e.g. Unraid)" (German:
  "Hermes im Docker-Container (z. B. Unraid)") for a Hermes installation in the Hermes Docker image:
  ingest and MCP by Compose, the MCP reached inside the Compose network at `http://mcp:8000/mcp/` (with
  `KORE_MCP_URL` in the server `.env`), `KORE_HERMES_DATA_DIR=/opt/data`, moved ports, and the coach
  bridge as a process in the Hermes container on a fixed container address, restarted without systemd.
- **Coach profile: consequences spelled out.** The README's profile step now names what an existing
  profile means: every way that profile is used (Telegram, groups, desktop, scheduled tasks) can read
  your health data through the read tools, while the bridge's limits apply only in the app chat. A new
  coach profile stays the recommendation. Optional extra step, never the default: a main assistant
  that gets only the read tools of the `kore` MCP server (`tools.include`).

### Changed

- **`kore-doctor` options before and after the subcommand.** `--bridge-env`, `--server-env` and
  `--state-dir` work in both positions (`kore-doctor --state-dir X devices` as well as
  `kore-doctor devices --state-dir X`) and without a subcommand; `--ingest-url` too.
- **`kore-doctor devices` only reads.** It no longer needs write access to the pairing state
  directory and creates no lock file (new `PairingStore.list_devices(lock=False)`; devices.json is
  replaced atomically, so a lock-free read is safe).
- **README "Updating" is now "Upgrading"** (anchor `#upgrading`; German "Aktualisieren",
  `#aktualisieren`), so the app can link to it. Steps: check out the tag, verify the commit hash,
  `docker compose up -d --build`, restart the bridge (systemd or container process), `kore-doctor`,
  `hermes -p <profile> mcp test kore`; paired devices stay paired and new variables are optional.
- **Docs follow 0.14.** The tool list is 9 read tools plus `propose_training_plan` (`mcp test` shows
  10 tools, was 8), with `get_activity_body_trend`, `get_nutrition_trend` and the new
  `get_coach_briefing` keys; the ingest's `GET /capabilities`, the new data categories (steps,
  weight and body fat, and nutrition only for your own coach after you share it in Kore), the moved
  ports, `KORE_HERMES_DATA_DIR` and the doctor's new behaviour are described where they belong.
- **Dependencies updated** (lockfiles regenerated with hashes): FastMCP 4.0.11 (on the MCP Python SDK 2.3.0;
  the HTTP client library `httpx` is replaced by `httpx2`), FastAPI 0.143.0, Starlette 1.7.0,
  Pydantic 2.14.0 (pydantic-core 2.50.0), Uvicorn 0.54.0. The MCP server behaves as before for
  Hermes (older MCP protocol versions are still served, same `/mcp` route, same bearer token check).
  CI actions: `actions/checkout` v7.0.1 and `actions/setup-python` v7.0.0, still pinned by commit SHA.

### Upgrading from 0.13.0

Pull the release, rebuild the containers and restart the bridge (`docker compose up -d --build`,
`sudo systemctl restart kore-coach-bridge`), then run `kore-doctor`. No `.env` or `bridge.env`
changes are needed; paired devices stay paired. The README's "Upgrading" section has the steps,
including how to verify the tag's commit hash and how to restart a bridge that runs in the Hermes
Docker container.

`hermes -p <profile> mcp test kore` now lists 10 tools (before: 8). All new variables are optional:
`KORE_INGEST_PORT` and `KORE_MCP_PORT` in `.env` only if you want to move a host port,
`KORE_MCP_URL` in `.env` only for an MCP that lives in the Compose network, and
`KORE_HERMES_DATA_DIR` in `bridge.env` only for Hermes data outside `<HOME>/.hermes` (such as the
Docker image's `/opt/data`; with the hardened unit also add that folder to `ReadWritePaths`). The bridge
unit itself is unchanged.

A v0.13.0 server keeps working with app 0.14: the app reads `GET /capabilities`, gets a 404 there
and leaves out steps, weight, nutrition and Apple's HRV labels until you upgrade. The other way
round, app 0.13 against this server leaves the stored lists as they are.

## 0.13.0

First public release.

### Added

- **Pairing.** `kore-doctor pair` creates a one-time code (20 characters, valid 10 minutes, used up by
  the first successful pairing and dead after 1000 failed attempts with a well-formed code; at most 5
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
- **Web search in the health chat, on explicit request.** Optional JSON boolean `health_web_allowed`
  on `POST /coach` (synchronous and with `client_message_id`). `true` in the health scope
  (`health_context_allowed: true`, consent scope 2) gives that turn `web,skills,memory,kore`
  (`web,skills,memory` without an origin ticket); absent or `false` keeps `skills,memory,kore`. Any
  other type is `400`; in the general scope the field is ignored; `POST /coach/adjust-today` answers
  `400` to it and never gets `web`. Switching it between the turns of one chat keeps the session. New
  optional capability `health_web_opt_in_v1` (not part of `contract/app-required-capabilities.json`),
  and `/health` reports the list as `app_chat_toolsets.health_with_web`; `kore-doctor` checks that it
  adds nothing but `web` to the health list. A turn with web search carries one fixed sentence at
  the end of its prompt (`[KORE-WEBSUCHE — BRIDGE-AUTORITATIV]`: no health values, names, notes or
  plan contents in search queries or URLs); a soft mitigation, not a barrier. Without the field the
  prompt is byte for byte unchanged. The person's own choice, at their own risk, off by default; see
  Residual risks in the README and `contract/health-web-opt-in-v1.md`.
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
  `web,skills,memory`. `/health` reports it under `app_chat_toolsets`. Only the explicit request
  `health_web_allowed: true` adds `web` to a health chat (see Added).
- **Hermes runs share one quota.** At most 2 run at once whichever route they come by; the
  synchronous `POST /coach` and `POST /coach/adjust-today` now answer `429` with `code: "busy"` when
  both places are taken, as the background turns did before. A turn keeps its place until Hermes has
  really ended.
- `coach-bridge/fixtures/` and the fixture notes no longer carry the model name or the memory
  provider of the machine they were recorded on (placeholders `<modell>` and `<provider>`).
- `kore-doctor` no longer has a built-in table of one provider and its program. It names the
  profile's model provider and the plugins under `plugins.enabled`; a program a provider plugin misses
  on the bridge's `PATH` is reported from Hermes' own start warning. Its tests use neutral fixtures.

### Security

Hardening from a security review of bridge, server and deployment before the release.

Coach bridge:

- **Fixed first line in every prompt.** Hermes reads a file path at the very start of a message
  (`/…`, `~/…`, `file://…`, `C:/…`) as an attachment, and an image file would reach the vision model
  past the tool list. Every prompt now begins with `[KORE-APP-NACHRICHT]`.
- **No `web` in the health scope.** Health context can no longer be sent out by a web tool; the
  adjustment of today's workout, which carries health context, lost it as well. `kore-doctor` fails
  when the bridge reports `web` for the health scope. Only the person's explicit request
  (`health_web_allowed`, see Added) lifts this for a health chat, never for the adjustment.
- **`skills.inline_shell` must be off.** With it on, Hermes runs shell commands from a skill's
  `SKILL.md`. The bridge does not start unless the profile's `config.yaml` has it missing or exactly
  `false`; `kore-doctor` checks it; the example profile sets it to `false`. Only plain YAML keys count
  as clear: a quoted key, a key with a backslash escape (`"inline\x5fshell"` spells the name without
  showing it) or a key in flow style anywhere in the file counts as on. The bridge checks the file
  again before every Hermes run; if it became unsafe after the start, every coach call gets
  `503 {"code": "profile_unsafe"}` (a background turn already accepted ends with `hermes_failed`) and
  the journal names the setting and the file.
- **"Only reduce" is enforced.** The adjusted workout of the day is compared with the one the app sent;
  more sets, weight, repetitions or duration, new or renamed exercises or another session id make the
  bridge answer `502` with a fixed sentence and return nothing of the reply. It used to be only an
  instruction in the prompt. The body carries `"code": "escalation"` next to the sentence, so the app
  can tell this rejection from other 502s without matching text. The returned session carries only
  `session_id`, `name` and `exercises` at its top level; whatever else the coach writes there is
  dropped.
- **One quota for every Hermes run** (see Changed) instead of a limit that covered only background
  turns.
- **Exactly one `Authorization` header** counts on the bridge, as on the ingest API: a request with two
  gets `401` (also on `POST /unpair`) before any of the body is read.
- **Connection cap and read deadline.** At most 64 connections at once, 20 s from the start of a
  connection for headers and body; a slow sender can no longer hold a thread open. The unit gets
  `TasksMax=512`, `MemoryMax=2G` and `LimitNOFILE=4096`.
- **Pairing codes.** A request without a well-formed code no longer uses up the open code; the code is
  used up by the first success and dead after 1000 failed attempts with it. (A limit of 5 added nothing
  against guessing 100 bits and let anyone in the network kill the open code.) The per-address limit
  checks and counts in one step, so parallel requests cannot exceed it.
- **Bridge token of at least 32 characters** (the token also keys the session references).
- **Forget and expiry check the source** of every listed session (`kore-<scope>`) before deleting it.
- **Forget and expiry reach pinned and archived sessions.** Hermes' `sessions prune` skips them unless
  asked; the bridge now selects with `--include-archived --include-pinned`. An open session that Hermes
  hides from `sessions list` (archived or hidden) cannot be found by its id: forgetting a scope counts
  it as `failed` instead of reporting success.
- **Hermes output is split only at line feeds.** Hermes writes JSON with `ensure_ascii=False`, so
  U+2028, U+2029 and U+0085 appear unescaped; `str.splitlines()` split records there. A reply
  containing one failed with `502 hermes_failed`, and a session whose title or history contained one
  could never be forgotten or expire. `kore-doctor` reads the `hermes --version` line the same way.
- **Each Hermes run gets a fresh, empty working directory** instead of the home directory, so
  `AGENTS.md`, `CLAUDE.md` and similar context files there are not loaded into the prompt.

Server:

- **Authentication before the body (ingest).** The bearer token is checked in front of the web
  framework, before any of the body is read. A request without a valid token used to make the server
  read and parse up to 16 MiB first; it now gets `401` with `Connection: close` at once. Default deny:
  only the exact path `/health` is open (at most 4 KiB); every other path, unknown ones and variants
  such as `/ingest%0A` included (Starlette's routing still matched those), needs the token first.
  Exactly one `Authorization` header counts.
- **The MCP server refuses two `Authorization` headers** with `401` before reading the body, like the
  ingest API and the bridge (FastMCP's own check would take the first).
- **Connection limits for ingest and MCP.** uvicorn gets `limit_concurrency=1024` (every further request
  is answered `503`; high enough that a handful of stalled connections cannot lock it) and `timeout_keep_alive=5`. uvicorn 0.48 has no deadline for headers or body; the
  README lists this under Residual risks.
- **No outbound call from the MCP server.** FastMCP asked PyPI for a newer release when it printed its
  start banner; `FASTMCP_CHECK_FOR_UPDATES=off` is now forced in `mcp_server.py` and set in
  `docker-compose.yml` and `deploy/kore-mcp.service`.
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
28 and loopback ports, the global (not per-address) connection limit, no deadline for headers or
body in ingest and MCP, no brake on guessing bearer tokens, the limits of the `skills.inline_shell`
check (Hermes' configuration overlay and backup), and that switching web search on in the middle of a
health chat exposes the earlier turns of that chat to the web-enabled run, not only the current
message (also in `contract/health-web-opt-in-v1.md`).

### Breaking for self-hosters

- **Tokens under 32 characters stop the services.** `KORE_BRIDGE_TOKEN`, `KORE_INGEST_TOKEN` and
  `KORE_MCP_TOKEN` need at least 32 characters (`openssl rand -hex 24` gives 48). Replace a short one,
  restart, and update the other side (Kore on the expert path, the profile's `.env` for the MCP token).
- **`skills.inline_shell` on stops the bridge.** Set it to `false` or remove it. A quoted, escaped or
  flow-style key anywhere in the profile's `config.yaml` stops it too; use plain keys.
- **Unknown paths on the ingest API answer `401` without a token** (before: `404`). Only `/health`
  is open.
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
- **Two `Authorization` headers are rejected** by the ingest API and the MCP server (before, the first
  one counted).
- **The health chat has no web search** any more; asking the coach in a health chat to search the web
  no longer works. Use the general chat for that, or switch web search on for the health chat in the
  app (`health_web_allowed`, at your own risk).
- **The bridge unit changed** (`TasksMax`, `MemoryMax`, `LimitNOFILE`); copy the unit from the README
  again.

### Upgrading from 0.10

See "Coming from 0.10 or earlier" in the [README](README.md#upgrading): run `kore-doctor init-state`,
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
