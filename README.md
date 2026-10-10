# kore-server

*English · [Deutsch](README.de.md)*

`kore-server` is the self-hosted backend for **Kore**, an iOS training and recovery app. It lets you
give Kore a coach of your own: an AI agent that you run yourself (Hermes Agent), on your own
machine, with your own model-provider key. The app sends a condensed daily summary of your health
data to your server, your agent reads it, chats with you inside Kore and can *propose* training
plans, which you confirm in the app.

Everything runs on your own hardware, in your own network, under your own tokens. There is no
service behind this that anyone else operates.

It is meant for people who already run an agent at home. If that is not you, you do not need this
repository: Kore works fully without a server.

Report security problems privately, see [SECURITY.md](SECURITY.md).

**Contents:** [What it is](#what-it-is) · [Requirements](#requirements) · [Setup](#setup) ·
[Pairing](#pairing-your-iphone) · [Remote access via VPN](#remote-access-via-vpn) ·
[Security](#security) · [Data and privacy](#data-and-privacy) ·
[Troubleshooting](#troubleshooting) · [kore-doctor](#checking-your-installation-kore-doctor) ·
[Updating](#updating) · [Reference](#reference) · [License](#license)

---

## What it is

Three small services run on one machine, next to your Hermes agent:

```
            iPhone (Kore)
              │         │
   chat       │         │  daily values
              ▼         ▼
      ┌──────────────┐  ┌────────────────┐
      │ Coach bridge │  │ Ingest API     │   Docker Compose
      │ :8090        │  │ :8081          │   (bridge: systemd)
      └──────┬───────┘  └───────┬────────┘
             │ starts           │ SQLite
             ▼                  ▼
      ┌──────────────┐  ┌────────────────┐
      │ Hermes Agent │─▶│ MCP server     │   127.0.0.1 only
      └──────────────┘  │ :8000          │
        reads Kore data └────────────────┘
        and proposes plans
```

| Service | Port | What it does | Reachable from |
| --- | --- | --- | --- |
| **Coach bridge** (`coach-bridge/`, plain Python, standard library only) | 8090 | Chat: Kore talks to the bridge, the bridge runs `hermes` for one dedicated profile and returns the answer. Also handles [pairing](#pairing-your-iphone). Runs as a systemd service on the host. | your home network or VPN address (`KORE_BRIDGE_BIND_HOST`) |
| **Ingest API** (`server/`, FastAPI, in Docker) | 8081 | Receives the daily values from the iPhone and hands pending plan proposals back. | your home network or VPN address (`KORE_BIND_ADDRESS`) |
| **MCP server** (`server/`, FastMCP, in Docker) | 8000 | Lets your Hermes profile *read* the Kore data (7 tools) and *propose* a training plan (1 tool). | **this machine only** (`127.0.0.1`) |

Kore talks to the bridge and the ingest API. Only Hermes talks to the MCP server.

Two helpers ship in this repository: `scripts/kore-doctor` checks the installation and manages
pairing, and `hermes/kore-app-chat-soul.md` is the section you add to your coach's `SOUL.md`.

> **What your coach says is not a statement from Kore.** It is a third-party AI. Kore did not write
> its sentences or check them, and for a plan it only checks form and limits, never content. Neither
> Kore nor your coach gives diagnoses.

## Requirements

| | |
| --- | --- |
| **Kore** | Version 0.13 or newer for pairing. Older versions (0.10 and up) work with the static-token setup (see [Pairing](#pairing-your-iphone)). |
| **A Linux host that stays on** | Home server, mini PC, NAS, Raspberry Pi 5 … on the same network as your iPhone. Docker with Compose v2 for ingest and MCP, Python 3 for the bridge. |
| **Hermes Agent 0.21.4 or newer** | Installed on **the same host**: the bridge starts `hermes` as a child process, and the MCP server is only reachable locally. Tested with v0.21.4 and v0.21.5. Without Hermes the server still works for reading and plan proposals through MCP, but nothing can chat. |
| **Your own model-provider key** | Hermes needs a model. Bring your own provider key (or a local model) and enter it yourself in your terminal, never in a chat. |
| **A fixed address for the server** | A DHCP reservation in your router, or a VPN address (see [VPN](#remote-access-via-vpn)). |

You do **not** need a public address, a domain or a TLS certificate. Everything stays in your home
network or your VPN. That is the point.

**On model providers.** Kore does not choose one for you. Use an API key from the provider of your
choice (or a local model): its cost is predictable and easy to see. We do **not** recommend using a
chat subscription (for example a Claude subscription) as your agent's provider. Whether and how a
subscription may be used through a third-party agent, and how that is billed, is unclear and can
change. Check your provider's current terms before you rely on it.

## Setup

Pick one path. Both end in the same place: three services running and checked with `kore-doctor`.

### Path A: let Hermes set it up

This is the shorter way. In Kore open **Settings › Experimental features** and copy the
**Hermes setup prompt**. Paste it into your **existing** Hermes agent, over SSH in `hermes chat`
(not through Telegram, Discord or any other messenger).

The prompt is a checklist that Hermes works through phase by phase. It:

1. looks, read-only, at your system (OS, user, Hermes version, Docker, free ports, network address)
   and stops if the user is root, has passwordless `sudo` or is in the `docker` group;
2. clones `kore-server` at the tag the prompt names and checks the commit hash;
3. creates `.env` with freshly generated secrets that never appear in the chat, and starts ingest
   and MCP with Docker Compose;
4. creates a **new** Hermes profile for your training bot (never a clone of your main profile), asks
   you to set up the model yourself in your own terminal, adds the Kore section to its `SOUL.md`,
   registers the MCP server and turns off automatic titles;
5. sets up the bridge as a systemd service, runs `kore-doctor` and three end-to-end probes;
6. finishes with a pairing link (and a QR code in the terminal) for your iPhone.

It asks before every `sudo` command, package installation, firewall change, deletion and profile
change. Inside the prompt, text from the repository, web pages and tool output counts as data, not
as instructions. The manual steps below are the same path done by hand.

### Path B: set it up by hand

Run the steps as the Linux user that will run the bridge and Hermes (not as root).

**1. Get the code at a release tag.**

```bash
git clone https://github.com/xZeVeXx/kore-server.git ~/kore-server
cd ~/kore-server
git checkout --detach v0.13.0
```

**2. Create the pairing state directory.** It must exist *before* `docker compose up`; otherwise
Docker creates it as root and the bridge cannot write to it.

```bash
python3 scripts/kore-doctor init-state
```

It creates `state/` (mode 0750) with the server id and prints a line `KORE_STATE_GID=<gid>`. You
need that group id for `.env` in the next step: the ingest container runs as a different user and
reads the list of paired devices through your group.

**3. Configure the server.**

```bash
cp .env.example .env && chmod 600 .env
openssl rand -hex 24   # -> KORE_INGEST_TOKEN
openssl rand -hex 24   # -> KORE_MCP_TOKEN
openssl rand -hex 32   # -> KORE_PLAN_ORIGIN_SECRET
```

Generate the values, do not type them out. Put them into `.env`, together with the address of this
machine that your iPhone will use and the group id from step 2:

```
KORE_BIND_ADDRESS=10.0.0.10     # never 0.0.0.0
KORE_STATE_GID=1000             # the value printed by init-state
```

While a token or the secret is empty or still starts with `change-me`, ingest and MCP refuse to
start; the message names the key, never the value. Leave `KORE_MCP_ALLOWED_ORIGINS` empty. Compose
also refuses to start without `KORE_STATE_GID`.

**4. Start ingest and MCP.**

```bash
docker compose up -d --build
curl -s 10.0.0.10:8081/health                                  # -> {"ok":true}
curl -s -o /dev/null -w '%{http_code}\n' 127.0.0.1:8000/mcp    # -> 401
```

The **401** is the right answer: the MCP port rejects everyone without a token. Without Docker, see
[`deploy/`](deploy/) (a dedicated system user, a virtualenv, systemd units).

**5. Create the Hermes profile for your coach.** Kore talks to exactly one profile.

```bash
HERMES=~/.hermes/hermes-agent/venv/bin/hermes     # or wherever `hermes` lives
$HERMES profile create kore-coach                 # no --clone / --clone-all
$HERMES -p kore-coach model                       # set up the model yourself, in your terminal
$HERMES -p kore-coach config set auxiliary.title_generation.enabled false
```

Never clone your main profile: that would copy its API keys, MCP servers and memories into the
coach. Then add three things to the new profile.

*The MCP server*, appended to `~/.hermes/profiles/kore-coach/config.yaml` (it is also in
[`hermes/kore-coach-profile.example.yaml`](hermes/kore-coach-profile.example.yaml)):

```yaml
mcp_servers:
  kore:
    url: http://127.0.0.1:8000/mcp/     # local only; the trailing slash matters
    headers:
      Authorization: Bearer ${MCP_KORE_API_KEY}
    enabled: true
    tools:
      resources: false
      prompts: false
    sampling:
      enabled: false
```

The token is **not** written into `config.yaml` but into the profile's own
`~/.hermes/profiles/kore-coach/.env` (`chmod 600`) as `MCP_KORE_API_KEY=<your KORE_MCP_TOKEN>`. Do
not put it into a systemd `Environment=` line: the bridge starts Hermes with a minimal environment
and passes no service variables on.

*The chat section of the coach's personality*,
[`hermes/kore-app-chat-soul.md`](hermes/kore-app-chat-soul.md). If `SOUL.md` already contains a
`## Kore-App-Chat` section, **replace** it up to the next `## ` heading instead of appending a
second one: an old section with an outdated plan schema makes the app silently discard proposals.
This command does either, after making a backup:

```bash
cd ~/.hermes/profiles/kore-coach && { [ ! -f SOUL.md ] || cp SOUL.md "SOUL.md.bak-$(date +%Y%m%d%H%M%S)"; } && python3 - <<'EOF'
from pathlib import Path
soul = Path("SOUL.md")
new = (Path.home() / "kore-server/hermes/kore-app-chat-soul.md").read_text().lstrip("\n")
text = soul.read_text() if soul.exists() else ""
start = 0 if text.startswith("## Kore-App-Chat") else text.find("\n## Kore-App-Chat") + 1
if start == 0 and not text.startswith("## Kore-App-Chat"):
    soul.write_text((text.rstrip("\n") + "\n\n" if text.strip() else "") + new)
else:
    end = text.find("\n## ", start + 1)
    soul.write_text(text[:start] + new + ("" if end < 0 else text[end + 1:]))
EOF
```

*Check:* `$HERMES -p kore-coach mcp test kore` must say *Connected* and *Tools discovered: 8*.

**6. Set up the coach bridge.**

```bash
mkdir -p ~/kore-coach-bridge && chmod 700 ~/kore-coach-bridge
cp ~/kore-server/coach-bridge/bridge.env.example ~/kore-coach-bridge/bridge.env
chmod 600 ~/kore-coach-bridge/bridge.env
```

Edit `bridge.env` (absolute paths only; systemd does not expand `$HOME` or `~`):

| Key | Value |
| --- | --- |
| `KORE_BRIDGE_TOKEN` | a new random value (`openssl rand -hex 24`) |
| `KORE_BRIDGE_BIND_HOST` | the same address as `KORE_BIND_ADDRESS` (`10.0.0.10`), never `0.0.0.0`. With `127.0.0.1` your iPhone cannot reach the bridge. |
| `KORE_BRIDGE_PORT` | `8090` |
| `KORE_HERMES_BIN` | absolute path to `hermes` |
| `KORE_HERMES_HOME` | the home directory of this user |
| `KORE_HERMES_PROFILE` | `kore-coach` |
| `KORE_INSTANCE_ID`, `KORE_PLAN_ORIGIN_PRINCIPAL` | the same values as in the server's `.env`; never change them later |
| `KORE_PLAN_ORIGIN_SECRET` | the same value as in the server's `.env` |

Never set `KORE_HERMES_ALLOW_ARGV_PROMPTS`. Then install the service as
`/etc/systemd/system/kore-coach-bridge.service` (replace `<USER>`, `<GROUP>` (its group, the one in `KORE_STATE_GID`) and `<HOME>`; the same unit is
`coach-bridge/kore-coach-bridge.service`):

```ini
[Unit]
Description=Kore Coach Bridge (App -> Hermes-Profil aus KORE_HERMES_PROFILE -> kore MCP)
Wants=network-online.target
After=network-online.target
StartLimitIntervalSec=0

[Service]
User=<USER>
Group=<GROUP>
WorkingDirectory=<HOME>
Environment=HOME=<HOME>
EnvironmentFile=<HOME>/kore-coach-bridge/bridge.env
ExecStart=/usr/bin/python3 <HOME>/kore-server/coach-bridge/bridge.py
Restart=on-failure
RestartSec=5
UMask=0077
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=<HOME>/.hermes <HOME>/kore-server/state -<HOME>/.cache
ProtectKernelTunables=yes
ProtectControlGroups=yes
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX

[Install]
WantedBy=multi-user.target
```

The bridge **writes** the pairing state (`<HOME>/kore-server/state`), so that path must be in
`ReadWritePaths`. Without it, `ProtectSystem=strict` makes pairing fail: the bridge starts, logs
`Device pairing unavailable` and answers every pairing attempt with 401. Then:

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now kore-coach-bridge
```

(If your state directory is not `<HOME>/kore-server/state`, set `KORE_STATE_DIR` in `bridge.env`
and `.env` to the same absolute path and use that path in `ReadWritePaths`; systemd does not expand
variables there.) If a firewall such as `ufw` is active, allow
only port 8090 from your local subnet. Docker bypasses `ufw` for its own ports, and the MCP port is
local anyway.

**7. Check everything.**

```bash
python3 ~/kore-server/scripts/kore-doctor
```

The exit code must be 0. See [kore-doctor](#checking-your-installation-kore-doctor). Then
[pair your iPhone](#pairing-your-iphone).

## Pairing your iPhone

Instead of typing URLs and tokens into the app, Kore **pairs** with your server using a one-time
code. On the server, as the bridge user:

```bash
python3 ~/kore-server/scripts/kore-doctor pair
```

It prints a link of the form `kore://pair?v=1&bridge=…&ingest=…&code=…&sid=…` and, if `qrencode`
is installed (`apt install qrencode`), a QR code in the terminal. Then either

- **tap the link** on your iPhone (send it to yourself over a channel you trust), or
- **scan the QR code** with Kore (Settings › Experimental features).

Kore asks "Pair with this server?" and shows the address and the server id. After you confirm, it
exchanges the code for a **device token**, stores it in the iOS Keychain and shows "Paired since …".
The exact wording of the buttons may differ slightly between app versions.

How it works and what it guarantees:

- The code has 20 characters (100 bits), is valid for **10 minutes**, and is used up by the **first**
  attempt, successful or not. Creating a new code replaces the open one. After 5 failed attempts per
  minute from one address the server answers 429.
- The link never contains a long-lived token. A device token is 256 bits of randomness; the server
  stores only its SHA-256 hash, in the state directory, and logs neither codes nor tokens.
- The device token is valid for the bridge and the ingest API, nothing else. The MCP server accepts
  only its own token, which devices never see.
- A link that someone intercepts is useless outside your home network or VPN (the server is not
  reachable there), works only once and expires after 10 minutes.

Useful options: `--name "My iPhone"` labels the device that pairs next; `--bridge-url` and
`--ingest-url` set the addresses if they cannot be derived from `bridge.env` and `.env`. Accepted are
private (10/8, 172.16/12, 192.168/16) and VPN (100.64/10) IPv4 addresses, and `https://<name>.ts.net`
(Tailscale serve, see [VPN](#away-from-home-vpn)); never loopback, other host names or public addresses.

**Managing devices:**

```bash
python3 ~/kore-server/scripts/kore-doctor devices         # id, name, paired, last seen (never tokens)
python3 ~/kore-server/scripts/kore-doctor revoke 1a2b3c4d  # lock one device out at once
```

A revoked device is rejected on its very next request, by the bridge and the ingest API alike.
"Unpair" in the app removes the token on the phone and tells the server to forget the device. Lost
or replaced a phone? `revoke` it. The full protocol is in [`contract/pairing-v1.md`](contract/pairing-v1.md).

**Expert path with static tokens.** The static tokens (`KORE_INGEST_TOKEN`, `KORE_BRIDGE_TOKEN`) stay
valid. In Kore, **Settings › Experimental features**, enter four values by hand: the server URL
(`http://10.0.0.10:8081`), the ingest token, the coach bridge URL (`http://10.0.0.10:8090`) and the
coach token. Use this if you prefer it, if your Kore is older than 0.13 or if pairing is not
available. Transfer the tokens only over a channel you trust, never through a messenger or notes
app; Kore strips spaces, line breaks and a leading "Bearer" when you paste.

Then, in Kore: allow Apple Health access, turn on **Coach active** and **Sync health snapshot**,
optionally **Share health context with coach** (it asks again), allow the iOS "Local network"
prompt, tap **Sync now** and **Test coach bridge**. Send your first message with the coach button in
the header. A good first question is one whose answer you can check yourself: *"How was my recovery
this week, and what do you notice about my sleep?"*

## Remote access via VPN

At home Kore reaches the server directly over your home network. Away from home, use a **VPN into
your home network**. The rule: **never forward a port, never bind to `0.0.0.0`, never use a public
tunnel service** (reverse tunnels, Tailscale Funnel and similar). All three services speak plain
HTTP; inside a VPN tunnel that traffic is encrypted by the tunnel, at home it is not.

The services listen on **one** address each, so pick the address your iPhone should always use.

### Option 1: WireGuard on your router (for example FRITZ!Box)

Many routers can act as a WireGuard server. Create a connection for your iPhone in the router's VPN
settings, import the configuration into the WireGuard app on the iPhone, and make sure the tunnel
routes your home subnet (the `AllowedIPs` of the iPhone's configuration include it, for example
`10.0.0.0/24`). The phone then reaches the server under the **same address as at home**
(`10.0.0.10`). Nothing changes on the server: keep `KORE_BIND_ADDRESS` and `KORE_BRIDGE_BIND_HOST` on
the LAN address (with a DHCP reservation) and pair as usual. The router's VPN port is the VPN's own
endpoint, not a forwarded Kore port.

### Option 2: Tailscale, step by step

Tailscale gives each of your devices a stable address in `100.64.0.0/10`, which the bridge and Kore
accept as a VPN address.

1. **Install Tailscale on the server** following Tailscale's instructions for your distribution, and
   sign in (`sudo tailscale up`).
2. **Install the Tailscale app on your iPhone** and sign in with the same account.
3. **Choose an unremarkable machine name** (in the admin console, or
   `sudo tailscale set --hostname=<name>`). If you ever enable HTTPS certificates, the machine name
   and your tailnet name appear in public certificate transparency logs that anyone can search. So
   not `<your-name>-health-server`, but something neutral.
4. **Find the server's Tailscale address:** `tailscale ip -4`, for example `100.64.0.10`.
5. **Bind to it.** In the server's `.env`: `KORE_BIND_ADDRESS=100.64.0.10`. In `bridge.env`:
   `KORE_BRIDGE_BIND_HOST=100.64.0.10`. Then restart: `docker compose up -d` and
   `sudo systemctl restart kore-coach-bridge`.
6. **Order at boot.** The address exists only once Tailscale is up. For the bridge, add
   `After=tailscaled.service` to the unit (`sudo systemctl edit kore-coach-bridge`); it also retries
   every five seconds on its own. If the Docker services did not come up after a reboot, run
   `docker compose up -d` again.
7. **Pair.** `kore-doctor pair` reads the addresses from `.env` and `bridge.env`, so the link already
   contains `100.64.0.10`. Switch Tailscale on on the phone, then open the link.
8. **Keep your tailnet personal** and its access rules tight: everything that can reach this
   address can try your tokens.

With the Tailscale app on, the same address works at home and away.

**Option 2b: HTTPS with `tailscale serve` (advanced).** Keep ingest and bridge on `127.0.0.1` and let
Tailscale publish them inside your tailnet with a certificate, for example:

```bash
sudo tailscale serve --bg --https=443  http://127.0.0.1:8081    # ingest
sudo tailscale serve --bg --https=8443 http://127.0.0.1:8090    # bridge
```

Check `tailscale serve --help` for the flags of your version; HTTPS certificates must be enabled in
the admin console. Kore then connects to `https://<machine>.<tailnet>.ts.net` and
`https://<machine>.<tailnet>.ts.net:8443`. `kore-doctor pair` accepts these names too
(`--bridge-url https://<machine>.<tailnet>.ts.net:8443 --ingest-url https://<machine>.<tailnet>.ts.net`),
but only with `https://`; any other host name is refused. The certificate-transparency note above applies.

## Security

### Principles

These points are not recommendations. They are the assumptions the code was written under. If you
violate them, you are running something other than the system described here.

1. **Only the condensed snapshot, never raw health data.** The app sends daily values: recovery,
   one HRV value per day (SDNN in ms, also as a history over the last days), resting heart rate,
   sleep hours, workout summaries. No beat-to-beat intervals, no measurement series within a day, no
   HealthKit exports. The MCP tools pass on exactly these values and cannot hand out anything else,
   because nothing else is there.
2. **Writing only as a plan proposal, confirmed in the app.** The only writing tool is
   `propose_training_plan`. It stores the plan with `status=pending`. The app fetches it, shows it
   to you, and only your tap on "Accept" makes it effective. An agent cannot silently activate
   anything in Kore. Every write also needs a short-lived, HMAC-signed one-time ticket from the
   coach bridge (`KORE_PLAN_ORIGIN_SECRET`); the MCP token alone is not enough.
3. **No port forwarding.** All ports belong on your home network, a VPN address or loopback. The
   bridge refuses to listen on `0.0.0.0` and accepts only a concrete IPv4 loopback, RFC 1918 or VPN
   (100.64.0.0/10) address. The MCP port is published on `127.0.0.1` only and is not reachable from
   your network at all.
4. **Separate tokens, and you rotate them.** Ingest, bridge and MCP each have their own static
   token, so a compromised agent cannot write what the app may write. See
   [Rotating secrets](#rotating-secrets).
5. **The `Origin` header is checked.** A web page you have open in a browser could otherwise talk to
   your local server (DNS rebinding). A separate ASGI layer (`server/origin_guard.py`) sits in front
   of ingest and MCP, and the bridge's pairing routes reject any request that carries an `Origin`.
   Requests without `Origin` (the app, the agent, `curl`) pass; a present `Origin` must be listed in
   `KORE_MCP_ALLOWED_ORIGINS` (empty by default, i.e. "no browser at all"), otherwise 403. It does not
   replace the token; it is the second lock on the same door.
6. **The prompt goes through stdin, not the command line.** Everything in `argv` is readable by every
   local user in `/proc/<pid>/cmdline` while the process runs. For a health chat that would be the
   health context. The bridge runs `hermes chat --query-file -` and writes the prompt to stdin. With
   `KORE_HERMES_ALLOW_ARGV_PROMPTS=1` the bridge refuses every coach call (503) and warns at startup.
7. **Tool poisoning.** An MCP client decides what to do from tool *descriptions*. If you register a
   malicious MCP server in the same profile that sees `kore`, it can hide instructions in its tool
   descriptions. In the profile that sees `kore`, use only tool servers whose source you know. This
   server cannot solve that for you; it can only keep small what there is to fetch (point 1).
8. **Pairing is single-use, short and revocable.** Codes are random, valid 10 minutes, consumed on
   the first attempt and rate-limited. Device tokens are random, stored only as hashes and can be
   revoked at any time. Neither codes nor tokens are logged.

### Network

- **No port forwarding. Ever.** What you would expose is a health history with a single bearer token
  in front of it. Away from home, use a [VPN](#remote-access-via-vpn).
- **Plain HTTP.** All three services speak HTTP without TLS. Anyone who can read your network sees
  tokens and content. Use it only in a network you trust; guests and strange devices belong on a
  separate Wi-Fi. A VPN tunnel encrypts the leg through the internet.
- **Leave `KORE_MCP_ALLOWED_ORIGINS` empty** unless you really put your own web interface in front.
- **Secrets are stored in plain text.** `.env`, `bridge.env` and the profile's `.env` are `chmod 600`,
  owned by the service user, never in a backup that ends up elsewhere, never in a git repo, a
  screenshot or a chat window. `.gitignore` keeps `*.env`, databases, `*.bak*` and key files out of
  git, `.dockerignore` out of the image.
- **Supply chain.** Dependencies install only from hash-locked files (`--require-hashes`), the base
  image is pinned by digest, GitHub Actions are pinned to commit SHAs and a gitleaks job scans the
  history for secrets.

### Rotating secrets

| Secret | Where | Who else needs it |
| --- | --- | --- |
| `KORE_INGEST_TOKEN` | server `.env` | Kore, only on the expert path |
| `KORE_MCP_TOKEN` | server `.env` | the coach profile's `.env` as `MCP_KORE_API_KEY` |
| `KORE_BRIDGE_TOKEN` | `bridge.env` | Kore, only on the expert path |
| `KORE_PLAN_ORIGIN_SECRET` | server `.env` **and** `bridge.env` (same value) | nobody else |
| device tokens | server-side only as hashes | `kore-doctor revoke <id>` cancels one |

Generate a new value (`openssl rand -hex 24`, for the origin secret `-hex 32`), enter it, restart
(`docker compose up -d --force-recreate`, `sudo systemctl restart kore-coach-bridge`), update the
other side, and run `kore-doctor`. Do it when you change agents, give a machine away, have pasted a
token somewhere it does not belong or have any suspicion. A new `KORE_BRIDGE_TOKEN` also invalidates
all chat references: every chat starts with "Context restarted", and the old Hermes sessions expire
after 30 days. Never change `KORE_INSTANCE_ID` or `KORE_PLAN_ORIGIN_PRINCIPAL` once data exists.

### What the bridge may and may not do

| It may | It may not |
| --- | --- |
| run `hermes` for exactly the one profile in `KORE_HERMES_PROFILE`, as its service user, with the prompt on stdin | enable terminal, files, code execution, cron jobs, computer or browser control in the app chat; the tools come from a fixed list (`-t`), not from a block list |
| allow `web,skills,memory` in the general chat, and additionally `kore` in a health chat with an origin ticket | pass service secrets on to Hermes: the child gets only `HOME`, `PATH` and seven keys such as `LANG`/`TZ` |
| read sessions of the sources `kore-general`/`kore-health` to find titles and predecessors, and delete them on request or after 30 days | touch sessions of other sources, or change the profile's memory or configuration |
| write one `kore-turn-failed` line (class, code, exit) to the journal when a turn fails | write prompts, answers, tokens, health values or Hermes stderr to the journal or to the app |
| run at most 2 turns at once and keep 64 answers in memory for up to 24 hours | start without token, origin secret and principal, listen on a public address, or gain privileges through `sudo`/setuid (`NoNewPrivileges=yes`) |

Fixed limits: a request is at most 256 KiB, a prompt 120 KiB, Hermes output 512 KiB, run time 150 s,
with a 30 s socket timeout against hanging connections; the ingest API accepts at most 16 MiB.
Tokens are compared in constant time.

### Residual risks

Kore sends health context only in chats with health sharing turned on, and the bridge hands out the
`kore` MCP only there. What your bot does with it afterwards, Kore does not control:

- **Your model provider sees everything in the prompt**, in a health chat that is the health
  context. You choose the provider in the profile.
- **Memory.** The bot can note something from a health chat with `memory`. Such notes flow into every
  later session of the profile, including general app chats and other channels of the same profile
  (for example Telegram). Deleting or expiry in Kore does not reach them; you manage this memory in
  Hermes.
- **External memory provider.** If the profile uses one, `memory` brings its tools along, and turns
  including the health context may go there. Check with `hermes -p <profile> memory status`.
- **Skills.** `skill_manage` belongs to `skills`. The bot can create or change skills of the profile,
  also when a text from a web search talks it into it. Such a skill keeps working in all channels of
  the profile. Look at new or changed skills if something seems strange.
- **Searching earlier chats.** The app chat has no `session_search`. Other channels of the same
  profile can still find Kore sessions with their own search until they are deleted or expire. A
  profile used only for Kore avoids this.
- **Stopped or discarded requests.** A stop ends Hermes, but whatever reached the provider and the
  session until then stays there until deletion or expiry (30 days).
- **Automatic titles.** If `auxiliary.title_generation` is on, the title model sees the first
  message including the health context. The example configuration turns it off.
- **Plugins, hooks and `SOUL.md` of the profile** run along (no `--safe-mode`). They are your
  configuration, not the chat's; the tools are still limited by the `-t` list.
- **A new Hermes toolset** could fall under an allowed name after an update. Run `kore-doctor` after
  every Hermes update: it flags every terminal, file, code, cron, computer or browser tool in the
  app-chat lists. Your agent should not run as root, with passwordless `sudo` or in the `docker`
  group anyway.
- **Pairing state on disk.** `devices.json` (hashes of device tokens only) is readable by the group
  you gave Docker (`KORE_STATE_GID`). Hashes of 256-bit tokens cannot be turned back into tokens;
  still, treat everyone in that group as trusted.

## Data and privacy

Your data stays on **your** server. Kore sends your health data only to the server you pair, and
this repository contains no analytics, telemetry or outbound calls of its own. What each part
stores:

| Where | What |
| --- | --- |
| **Ingest + MCP**, Docker volume `kore-db` (SQLite) | the latest snapshot from the app: profile basics, recovery, HRV daily values up to 30 days, resting heart rate, sleep, workouts with heart rate and calories, training plans with names and notes; pending plan proposals |
| **Pairing state**, `state/` next to `docker-compose.yml` | the server id, the hash of an open one-time code, and for each paired device an id, a name, the hash of its token and two timestamps. No tokens or codes in clear text |
| **Coach bridge** | no database. Running turns live in memory (up to 24 hours, 64 answers) and are lost on restart. The journal gets a one-line failure record without content |
| **Hermes profile**, `~/.hermes/profiles/<profile>/` | the chat sessions of Kore (sources `kore-general`/`kore-health`) and the profile's memory. The bridge deletes Kore sessions after 30 days without activity and when you delete a chat in Kore; it never touches memory |
| **Your model provider** | the prompts, and with "Share health context with coach" turned on, the health context in each message. That is how a chat works; choose your provider accordingly |

Without the "Share health context" switch, the coach receives only what you type.

## Troubleshooting

Kore names the next step under Experimental features. Test the bridge there with **Test coach bridge**.

| Kore or the server says | What is really going on | What to do |
| --- | --- | --- |
| "The server is not answering. Is `docker compose up` running?" | No answer at all: the service is off or the address points elsewhere. | `docker compose ps`. If it runs, check `KORE_BIND_ADDRESS`: with `127.0.0.1` only this machine reaches it, not your iPhone. |
| "Token rejected. Check the ingest token in .env." | The server answered and turned you away (401/403). The address is right. | Pair again, or compare `KORE_INGEST_TOKEN` with the field in Kore; after a change run `docker compose up -d`. A revoked device is rejected the same way. |
| "Coach bridge unreachable. Is the kore-coach-bridge service running, and does Kore have access to the local network?" | The bridge is its **own** service, not part of Compose; or iOS has not allowed Kore's local network access. | `systemctl status kore-coach-bridge`, or run `python3 coach-bridge/bridge.py` in the foreground and read the first line. If it does not start, the line names the missing key or one still set to `change-me`. On the iPhone: Settings › Kore › Local Network. Away from home: is the VPN on? |
| "Coach token rejected. Check KORE_BRIDGE_TOKEN in bridge.env and paste it without “Bearer”." | The bridge answered and turned you away. | Pair again, or paste `KORE_BRIDGE_TOKEN` without the word "Bearer". |
| "The bridge is running but not fully set up (503)." | It answers but has no token; deliberately an error rather than open access. | Set `KORE_BRIDGE_TOKEN` in `bridge.env` and restart. |
| "Your coach bridge is too old (API 1, needs 2). Update kore-server." | The bridge speaks an older protocol version. | Update kore-server to the version Kore names and restart the bridge. |
| "Your coach bridge is missing: …" | The bridge does not report a capability Kore requires; the name is in the text. | Update kore-server. If `plan_origin_v1` is missing, also check `KORE_PLAN_ORIGIN_SECRET` and `KORE_PLAN_ORIGIN_PRINCIPAL` in `bridge.env`; both must be set. If `device_pairing_v1` is missing, the state directory is unusable (see the two pairing rows below). |
| Pairing fails (401 `pairing_failed`) | The code is wrong, expired (10 minutes), used up by an earlier attempt, or pairing is unavailable on the server. | Run `kore-doctor pair` again for a fresh link. If it keeps failing: `journalctl -u kore-coach-bridge` and look for `Device pairing unavailable`. |
| Pairing: 429 `rate_limited` | Five failed attempts within a minute from one address. | Wait a minute and use a new code. |
| Journal: `Device pairing unavailable: …` | The state directory is missing or the bridge cannot write to it. | `kore-doctor init-state`, then check ownership. With the hardened unit, `<HOME>/kore-server/state` must be in `ReadWritePaths`. |
| `docker compose up` stops with "set KORE_STATE_GID …" | `KORE_STATE_GID` is missing in `.env`. | Run `kore-doctor init-state` and copy the printed line into `.env`. |
| Paired, but the ingest API answers 401 | Ingest cannot read `devices.json`: the group does not match. | `kore-doctor` has a "state directory" check; make `KORE_STATE_GID` the directory's group and run `docker compose up -d --force-recreate`. |
| `kore-doctor pair` refuses an address | Accepted are private or VPN IPv4 addresses and `https://<name>.ts.net`, not `127.0.0.1`, other host names, `http` on a name or public addresses. | Set `KORE_BRIDGE_BIND_HOST` and `KORE_BIND_ADDRESS` to the address your iPhone uses, or pass `--bridge-url` and `--ingest-url`. |
| "The bridge answered incompletely." | An answer arrived, but not a readable one. | Does the address really point to the bridge (port 8090) and not to another service? Then `journalctl -u kore-coach-bridge`. |
| "Warning: your bridge passes prompts on the command line. Remove KORE_HERMES_ALLOW_ARGV_PROMPTS from bridge.env." | The bridge is ready but would pass the prompt through `argv` (principle 6). | Delete the line from `bridge.env` and restart the bridge. |
| "The server answered with error 500." | Something inside the server. | `docker compose logs -f ingest`; the status number is what you search for. |
| A chat fails with `hermes_failed`, although `hermes` works in your shell | The bridge starts Hermes with a minimal `PATH`; your model provider needs a program outside it. | Put that program's folder first in `KORE_HERMES_CHILD_PATH` in `bridge.env`; `kore-doctor` checks it. |

Two things are as they are: the **401** on `127.0.0.1:8000/mcp` is correct (the port lives and
rejects strangers), and **"Connected" does not mean "answering"**: only **Sync now** tells you that.

## Checking your installation: kore-doctor

```bash
python3 scripts/kore-doctor                 # as the bridge user; defaults: ~/kore-coach-bridge/bridge.env and <repo>/.env
python3 scripts/kore-doctor --bridge-env /path/bridge.env --server-env /path/.env --ingest-url http://10.0.0.10:8081
python3 scripts/kore-doctor --bridge-only   # only bridge and Hermes
```

It needs only the standard library and never prints a secret. It checks that

- the ingest `/health` returns 200 (and 403 with a foreign `Origin`);
- the MCP server on `127.0.0.1` returns 401 without a token and is not reachable via the network address;
- the bridge returns 401 without a token, and its `/health` with a token reports every capability in
  [`contract/app-required-capabilities.json`](contract/app-required-capabilities.json),
  `prompt_transport: stdin` and `app_chat_toolsets` without terminal, file, code, cron, computer and
  browser tools;
- `KORE_PLAN_ORIGIN_SECRET` (compared by hash), `KORE_INSTANCE_ID` and `KORE_PLAN_ORIGIN_PRINCIPAL` are
  equal in server and bridge;
- the profile exists, `hermes -p <profile> mcp test kore` succeeds, Hermes runs with exactly the
  environment the bridge gives it, and the profile's `SOUL.md` contains the current
  [`kore-app-chat-soul.md`](hermes/kore-app-chat-soul.md);
- the pairing state directory exists with the right permissions and `KORE_STATE_GID` is its group.

Exit code 0 only without any deviation. `--bridge-only` leaves out ingest, MCP and the server `.env`,
for example when they run on another machine. The other sub-commands are `init-state`, `pair`,
`devices` and `revoke` (see [Pairing](#pairing-your-iphone)). Most messages are in German; the
pairing hint is bilingual.

## Updating

```bash
cd ~/kore-server
git fetch --tags origin && git checkout --detach <new tag>
docker compose up -d --build
sudo systemctl restart kore-coach-bridge
python3 scripts/kore-doctor
```

Run `kore-doctor` (and `hermes -p <profile> mcp test kore`) again after every `hermes update`, too.
Release notes are in [CHANGELOG.md](CHANGELOG.md).

**Coming from 0.10 or earlier:** run `python3 scripts/kore-doctor init-state` and add the printed
`KORE_STATE_GID=…` to `.env` (Compose refuses to start without it); add the state directory to
`ReadWritePaths` if you use the hardened unit; replace the `## Kore-App-Chat` section in the
profile's `SOUL.md` with the current file (`kore-doctor` flags an outdated one). The MCP server is
now published on `127.0.0.1` only.

## Reference

### The tools (7 read, 1 proposes)

| Tool | Returns |
| --- | --- |
| `get_recovery_today` | Today's values: recovery, HRV (with the method actually used), resting heart rate, sleep hours, band |
| `get_hrv_trend(days=30)` | One HRV value per day (SDNN in ms) for the last `days` days: daily values, no series within a day |
| `get_sleep_summary(days=7)` | Per night: total, deep and REM hours and efficiency |
| `get_recent_workouts(limit=10)` | Date, type, duration, average heart rate, volume |
| `get_active_plan` | The training plan currently active in the app |
| `get_training_plans` | All plans, plus the timestamp of the snapshot |
| `get_coach_briefing` | The condensed daily picture in one call: recovery, HRV, sleep, workouts, profile, plans |
| `propose_training_plan(plan, origin_ticket)` | Stores a plan as `pending`; the app confirms |

The HRV method is part of the key name: `hrv_rmssd_ms`, `hrv_sdnn_ms`, or `hrv_ms` if the method is
unknown. A value is never silently labelled as SDNN. Explicitly **not** delivered, because it is not
on the server at all: raw health values, location, contacts, photos, cycle details. Your coach cannot
delete, tick off or silently activate anything.

### Plan JSON

`propose_training_plan` takes a complete final plan, strictly validated with Pydantic:

```json
{ "schema_version": "1.0",
  "name": "Week 12, reduced",
  "rationale": "HRV four nights below baseline",
  "replaces_plan_id": "11111111-2222-4333-8444-555555555555",
  "replaces_plan": "Week 12",
  "base_plan_fingerprint": "sha256:aaaaaaaa…",
  "removed_session_ids": [], "removed_exercise_ids": [],
  "sessions": [
    { "session_id": "66666666-7777-4888-8999-AAAAAAAAAAAA", "name": "Push", "kind": "strength",
      "exercises": [
        { "exercise_id": "BBBBBBBB-CCCC-4DDD-8EEE-FFFFFFFFFFFF", "name": "Bench press",
          "note": "Barbell", "sets": [{ "weight_kg": 60, "reps": 8 }] } ] } ] }
```

- A **new** plan leaves out all update fields. An **update** needs `replaces_plan_id`,
  `base_plan_fingerprint` and **both** removal arrays, even if empty. The arrays must list exactly
  every removed identity after matching IDs.
- `replaces_plan_id` and `base_plan_fingerprint` may only come from a fresh, complete app context,
  never from the read-only MCP tools. An update against a stale state is rejected instead of silently
  overwriting.
- `kind: "cardio"` requires `exercises: []`; the goal is in `cardio_target`.
- `valid_from` does not exist and is rejected until Kore has real scheduling.

From proposal to plan: the agent calls `propose_training_plan`; the plan is validated and stored as
`pending`; Kore fetches it and shows it under *Experimental features › Coach proposals* with a "from
coach" capsule, exactly as it would be stored locally; only your tap on "Accept" makes it effective.
If you decline, nothing happens.

### Ingest API

`POST /ingest` (snapshot), `GET /plan/pending`, `POST /plan/{id}/ack` with `{"accepted": true|false}`,
`GET /health` (no token). Everything else needs `Authorization: Bearer <token>`, where the token is
`KORE_INGEST_TOKEN` or a paired device's token.

### Coach bridge protocol

The bridge is plain Python without dependencies and calls the `hermes` CLI as a bounded child process
(`hermes -p <profile> chat -Q --format stream-json --query-file - --source kore-<scope> -t <tools>
--run-budget <s> [--resume <id>]`). It is deliberately Hermes-specific, tested against Hermes Agent
v0.21.4 and v0.21.5. It reads the stream-json from stdout; answer and session come from the `result`
record, never from stderr. The fixtures in `coach-bridge/fixtures/hermes-v0.21.4/` come from real
runs; after a Hermes update run `test_bridge.py` and `test_runner_stream_json.py`.

If you write your own bridge for another agent, this is the small protocol the app speaks:

- `GET /health`, `GET /capabilities`: capability list, `api_version: 2`.
- `POST /pair` (no bearer) and `POST /unpair` (device token): see
  [`contract/pairing-v1.md`](contract/pairing-v1.md).
- `POST /coach` with `{message, conversation_id, session?, context?, health_context_allowed}`.
  `conversation_id` is a canonical lower-case UUID. `session` is a bridge-signed reference
  `general:<id>.<sig>` or `health:<id>.<sig>`, bound to scope and chat; if it does not match or
  Hermes no longer knows the session, the context starts anew (`context: "reset"`) instead of an
  error. Health context is passed on only if `health_context_allowed` is literally `true`
  (fail-closed).
- With an additional `client_message_id` (canonical UUID), `POST /coach` starts a turn job and answers
  at once `202 {turn_id, status}`; the same `client_message_id` in the same chat yields the same turn.
  A turn already running in the chat: `409`; more than two at once: `429`.
- `GET /coach/turns/{turn_id}/events`: server-sent events with `id:`; `Last-Event-ID` replays later
  events, `: keepalive` every 15 s. Only text deltas `{"delta": …}`, and at the end
  `{"done": true, "used_kore": …, "context": …, "session"?: …, "title"?: …}` or
  `{"error": …, "code": …}`; tool calls stay inside the bridge.
- `GET /coach/turns/{turn_id}` returns state or result (a finished turn is deleted with this answer);
  `POST /coach/turns/{turn_id}/cancel` ends Hermes, idempotent. The buffer lives in memory only, at
  most 24 hours.
- `POST /coach/adjust-today`: JSON answer with the adjusted workout of the day.
- `POST /coach/forget` with `{conversation_id, sessions: [<signed reference>, …]}` or
  `{scope: "health" | "all"}`: stops running turns, deletes their buffers and then the Hermes
  sessions including compression predecessors. Reply `{deleted, not_found, failed}`; deletion is
  confirmed only with `failed: 0`.
- Expiry: Kore sessions without activity for `KORE_BRIDGE_SESSION_RETENTION_DAYS` days (default and
  maximum 30) are deleted at startup and daily, never while a turn runs.
- Without a valid `Authorization: Bearer` (static bridge token or device token): 401. An unfinished
  installation is an error, never open access: if `KORE_BRIDGE_TOKEN`, `KORE_PLAN_ORIGIN_SECRET` or
  `KORE_PLAN_ORIGIN_PRINCIPAL` is empty or still `change-me…`, the bridge does not start and names the
  key (never the value).
- Tools per `-t` list instead of `--safe-mode` (`/health` reports them as `app_chat_toolsets`):
  general `web,skills,memory`, in a health chat with an origin ticket additionally `kore`. Without a
  ticket, no kore MCP. Never terminal, files, code execution, cron jobs, computer or browser control.

### Other MCP clients

The MCP server is reachable on `127.0.0.1` only. Another MCP client (for example a desktop chat app)
works only if it runs on the same machine, or if you set up an SSH tunnel to port 8000 yourself.
Connectors that need a public HTTPS address and OAuth (such as the web connectors of hosted
assistants) are a different mode of operation with a different risk, and deliberately not part of
this repository.

## What this repository is not

- **Not a multi-tenant service.** One set of processes, one database, one person. Two people in a
  household means two installations with their own tokens and their own `KORE_INSTANCE_ID`.
- **Not a path to hosted assistants.** See above.
- **Not medical software.** Neither Kore nor your coach gives diagnoses.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for tests, lockfiles and what never belongs in an issue.
Please do not post health data or tokens anywhere.

## License

MIT, see [LICENSE](LICENSE).
