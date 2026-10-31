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
[Hermes in Docker](#hermes-in-a-docker-container-eg-unraid) · [Pairing](#pairing-your-iphone) ·
[Remote access via VPN](#remote-access-via-vpn) ·
[Security](#security) · [Data and privacy](#data-and-privacy) ·
[Troubleshooting](#troubleshooting) · [kore-doctor](#checking-your-installation-kore-doctor) ·
[Upgrading](#upgrading) · [Reference](#reference) · [License](#license)

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
| **Coach bridge** (`coach-bridge/`, plain Python, standard library only) | 8090 | Chat: Kore talks to the bridge, the bridge runs `hermes` for one dedicated profile and returns the answer. Also handles [pairing](#pairing-your-iphone). Runs as a systemd service on the host. | your home network address, or `127.0.0.1` behind `tailscale serve` (`KORE_BRIDGE_BIND_HOST`) |
| **Ingest API** (`server/`, FastAPI, in Docker) | 8081 | Receives the daily values from the iPhone, names the snapshot fields it takes (`GET /capabilities`) and hands pending plan proposals back. | your home network address, or `127.0.0.1` behind `tailscale serve` (`KORE_BIND_ADDRESS`) |
| **MCP server** (`server/`, FastMCP, in Docker) | 8000 | Lets your Hermes profile *read* the Kore data (9 tools) and *propose* a training plan (1 tool). | **this machine only** (`127.0.0.1`) |

Kore talks to the bridge and the ingest API. Only Hermes talks to the MCP server.

The ports are the defaults inside the containers. On the host, Docker Compose publishes ingest and MCP
on `KORE_INGEST_PORT` and `KORE_MCP_PORT` from your `.env` (default 8081 and 8000); set them when a
port is taken or a second stack runs on the same machine.

Two helpers ship in this repository: `scripts/kore-doctor` checks the installation and manages
pairing, and `hermes/kore-app-chat-soul.md` is the section you add to your coach's `SOUL.md`.

> **What your coach says is not a statement from Kore.** It is a third-party AI. Kore did not write
> its sentences or check them, and for a plan it only checks form and limits, never content. Neither
> Kore nor your coach gives diagnoses.

## Requirements

| | |
| --- | --- |
| **Kore** | Version 0.13 or newer for pairing. Older versions (0.10 and up) work with the static-token setup (see [Pairing](#pairing-your-iphone)). Steps, weight and nutrition for the coach need Kore 0.14 and this server version; an older server keeps working with a newer Kore, which then leaves those out. |
| **A Linux host that stays on** | Home server, mini PC, NAS, Raspberry Pi 5 … on the same network as your iPhone. Docker with Compose v2 for ingest and MCP (Docker 28 or newer is recommended, see [Network](#network)), Python 3 for the bridge. |
| **Hermes Agent 0.21.4 or newer** | Installed on **the same host**: the bridge starts `hermes` as a child process, and the MCP server is only reachable locally. Tested with v0.21.4 and v0.21.5. Without Hermes the server still works for reading and plan proposals through MCP, but nothing can chat. |
| **Your own model-provider key** | Hermes needs a model. Bring your own provider key (or a local model) and enter it yourself in your terminal, never in a chat. |
| **A fixed address for the server** | A DHCP reservation in your router (the services listen on that address; with Tailscale only you can use loopback instead, see [VPN](#remote-access-via-vpn)). |

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
4. asks whether to create a **new** Hermes profile for your training bot (the default, never a clone
   of your main profile) or to use an **existing** one; for an existing profile it first names the
   consequences (every way that profile is used can then read your health data, see step 5 of Path
   B) and needs your explicit yes. Then it asks you to set up the
   model yourself in your own terminal, adds the Kore section to the profile's `SOUL.md`, registers
   the MCP server and turns off automatic titles;
5. sets up the bridge as a systemd service, or, where there is no systemd (the Hermes Docker image,
   Unraid), as a process in the Hermes container
   ([Hermes in a Docker container](#hermes-in-a-docker-container-eg-unraid)), and runs `kore-doctor`
   and three end-to-end probes;
6. installs `qrencode` (after your OK) and finishes with a pairing link and a QR code in the terminal
   for your iPhone.

It asks before every `sudo` command, package installation, firewall change, deletion and profile
change. Inside the prompt, text from the repository, web pages and tool output counts as data, not
as instructions. The manual steps below are the same path done by hand.

### Path B: set it up by hand

Run the steps as the Linux user that will run the bridge and Hermes (not as root).

**1. Get the code at a release tag.**

```bash
git clone https://github.com/xZeVeXx/kore-server.git ~/kore-server
cd ~/kore-server
git checkout --detach v0.14.1
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
start; the message names the key, never the value. The same goes for a token shorter than 32
characters (`openssl rand -hex 24` gives 48) and for a `KORE_BIND_ADDRESS` that is not a concrete
loopback, private (RFC 1918) or VPN (`100.64.0.0/10`) address: with `0.0.0.0` or a public address the
ingest container stops with a message and, being set to restart, tries again in a loop; the reason
is in `docker compose logs ingest`. Leave `KORE_MCP_ALLOWED_ORIGINS` empty. Compose also refuses to
start without `KORE_STATE_GID`, and without any other key a container needs (it says which).

Compose reads `.env` only to fill in values: `docker-compose.yml` lists, per container, exactly the
keys that container gets (ingest does not see the MCP token or the origin secret, MCP does not see
the ingest token). A key you add to `.env` yourself reaches a container only after you add it to
that service's `environment:` list in `docker-compose.yml`.

*Moved ports (optional).* If port 8081 or 8000 is taken on the host, or a second stack runs on the same
machine, add `KORE_INGEST_PORT` and/or `KORE_MCP_PORT` to `.env`. Compose then publishes the ingest and
the MCP server on those host ports (the containers keep 8081 and 8000, the MCP stays on `127.0.0.1`).
`kore-doctor` and `kore-doctor pair` read both values; use the moved ports in the `curl` lines below,
in Kore's server address and in the profile's MCP `url` (step 5).

**4. Start ingest and MCP.**

```bash
docker compose up -d --build
curl -s 10.0.0.10:8081/health                                  # -> {"ok":true}
curl -s -o /dev/null -w '%{http_code}\n' 127.0.0.1:8000/mcp    # -> 401
```

The **401** is the right answer: the MCP port rejects everyone without a token. Without Docker, see
[`deploy/`](deploy/) (a dedicated system user, a virtualenv, systemd units; there the ingest API
listens on `KORE_BIND_ADDRESS` and the MCP server on `KORE_MCP_HOST`, default `127.0.0.1`).

**5. Choose the Hermes profile for your coach.** Kore talks to exactly one profile: either a new
coach profile (recommended) or one you already use.

*A new coach profile (default, recommended).* It starts empty: no memories, no other tool servers, only
what you give it. As long as you add no messenger or scheduled task to it, only the Kore app chat runs
in it, under the limits of the bridge (a fixed tool list per chat).

```bash
HERMES=~/.hermes/hermes-agent/venv/bin/hermes     # or wherever `hermes` lives
$HERMES profile create kore-coach                 # no --clone / --clone-all
$HERMES -p kore-coach model                       # set up the model yourself, in your terminal
$HERMES -p kore-coach config set auxiliary.title_generation.enabled false
```

Never clone your main profile: that would copy its API keys, MCP servers and memories into the
coach.

*Model providers that are Hermes plugins.* Some providers (for example a subscription plugin) are
delivered as a plugin, and plugins are enabled **per profile**. A new profile does not have them yet:
install and enable the plugin in the new profile (`$HERMES -p kore-coach …`, the same way you did for
your main profile) and sign in to the provider yourself in your terminal. Do **not** copy credential
files, `.env` or auth stores from another profile. If the plugin runs an extra program (a provider CLI
in `~/.local/bin`, say), also set `KORE_HERMES_CHILD_PATH` in step 6, otherwise chats fail with
`hermes_failed`.

*An existing profile (your choice, with consequences).* You can set `KORE_HERMES_PROFILE` to a profile
you already use, for example one with a memory you want to keep, and do the additions below in it. Do
this only if you accept all of the following:

- **Every way that profile is used can read your health data.** Telegram and other messengers, group
  chats, the desktop app and scheduled tasks all run in the same profile. Once the `kore` MCP server is
  registered in it, each of them can call the nine read tools (recovery, HRV, sleep, workouts, plans,
  steps, weight and body fat, and nutrition if you share it) without going through the app chat. The
  limits of the coach bridge (fixed tool list, no terminal, files or code, no web search next to
  health context) apply **only** to the app chat, not to these other ways.
- **Health data in its general memory.** Kore chats run inside that profile. With health context
  allowed, the coach can store what it learns in the profile's memory, and every later session of the
  profile (your other chats and channels included) can see it.
- **A Kore section in its `SOUL.md`.** The Kore section is added (after a backup) to the profile's
  personality file and stays part of the profile's instructions for everything it does.
- **An external memory provider (for example Honcho) receives Kore chats**, health context included,
  if the profile uses one. Check with `$HERMES -p <profile> memory status`.
- **Deleting in Kore does not reach all of it.** The bridge deletes the Kore *sessions*; copies in the
  profile's memory or at an external memory provider stay until you remove them there.

If in doubt, take a new profile. Then add three things to the profile.

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

*Check:* `$HERMES -p kore-coach mcp test kore` must say *Connected* and *Tools discovered: 10*. If it
fails with `No module named 'mcp'`, your Hermes lacks its MCP extra; `hermes pm install --extra mcp`
adds it (a Hermes matter, `kore-doctor` names the remedy too).

*Shell commands in skills stay off.* In the profile's `config.yaml`, `skills.inline_shell` must be
missing (the Hermes default) or exactly `false`:

```yaml
skills:
  inline_shell: false
```

With `true`, Hermes runs `` !`command` `` lines from a skill's `SKILL.md` through bash as soon as the
skill is shown, and the app chat has the `skills` toolset. The bridge therefore **does not start**
while the setting is on or not clearly off (`true`, `yes`, `"false"` in quotes, a variable, an
unusual layout; the message names the setting and the file), and `kore-doctor` flags it. Only plain
YAML keys count as clear anywhere in this file: a quoted key (`"skills":`), a key with a backslash
escape (`"inline\x5fshell"` spells `inline_shell`) or a key in a flow mapping (`{…}`) is treated as
on. The bridge checks again before every Hermes run: if the file becomes unsafe while the bridge is
running, every coach call is refused with `503` (`code: profile_unsafe`) until it is fixed, and the
journal names the setting and the file. If the bridge refused to start, start it again after fixing
the file.

*Optional extra step: let your main assistant read Kore too.* The coach profile is where the Kore chat
runs. If you also want the assistant you talk to every day (another profile) to answer questions from
your Kore values, you can register the `kore` MCP server in that profile as well, with the same
`url`, its own `MCP_KORE_API_KEY=<your KORE_MCP_TOKEN>` line in **that** profile's `.env`, and only the
read tools:

```yaml
mcp_servers:
  kore:
    url: http://127.0.0.1:8000/mcp/
    headers:
      Authorization: Bearer ${MCP_KORE_API_KEY}
    enabled: true
    tools:
      include: [get_recovery_today, get_hrv_trend, get_sleep_summary, get_recent_workouts,
                get_activity_body_trend, get_nutrition_trend, get_active_plan, get_training_plans,
                get_coach_briefing]
      resources: false
      prompts: false
    sampling:
      enabled: false
```

(`tools.include` is Hermes' own filter; check its documentation for your version. Leaving out
`propose_training_plan` keeps that profile from proposing plans at all.) This is an extra step, never
the default, and it has the same consequences as an existing profile above: every way that assistant
is used (messengers, groups, desktop, scheduled tasks) can then read your health values, **without**
the bridge's limits, and its memory and any external memory provider can keep them. Your model
provider sees whatever the assistant fetches. Rotating `KORE_MCP_TOKEN` then means updating this
profile's `.env` too. Skip it if you do not need it; to undo it, delete the `kore` entry again.

**6. Set up the coach bridge.**

```bash
mkdir -p ~/kore-coach-bridge && chmod 700 ~/kore-coach-bridge
cp ~/kore-server/coach-bridge/bridge.env.example ~/kore-coach-bridge/bridge.env
chmod 600 ~/kore-coach-bridge/bridge.env
```

Edit `bridge.env` (absolute paths only; systemd does not expand `$HOME` or `~`):

| Key | Value |
| --- | --- |
| `KORE_BRIDGE_TOKEN` | a new random value (`openssl rand -hex 24`), at least 32 characters; the bridge refuses a shorter one (the token also keys the chat session references) |
| `KORE_BRIDGE_BIND_HOST` | the same address as `KORE_BIND_ADDRESS` (`10.0.0.10`), never `0.0.0.0`. With `127.0.0.1` your iPhone cannot reach the bridge. |
| `KORE_BRIDGE_PORT` | `8090` |
| `KORE_HERMES_BIN` | absolute path to `hermes` |
| `KORE_HERMES_HOME` | the home directory of this user |
| `KORE_HERMES_DATA_DIR` (optional) | only if Hermes keeps its data somewhere other than `<home>/.hermes`, as the Hermes Docker image does (`/opt/data`). An absolute path without `..`, otherwise the bridge does not start. The bridge passes it to Hermes as `HERMES_HOME` (the only addition to the Hermes environment) and looks for the profile in `<dir>/profiles/<name>`; `kore-doctor` looks there too. With the systemd unit, also add the folder to `ReadWritePaths`. See [Hermes in a Docker container](#hermes-in-a-docker-container-eg-unraid). |
| `KORE_HERMES_WORK_DIR` (optional) | where the Hermes runs work. Default: `kore-bridge-work` in the Hermes data folder (`<home>/.hermes` or `KORE_HERMES_DATA_DIR`); the bridge uses its subfolder `chat` and empties it before and after every run. Set it only if the Hermes gateway cannot see that folder. Never inside a Git checkout; with the systemd unit, also add it to `ReadWritePaths`. |
| `KORE_HERMES_PROFILE` | `kore-coach` |
| `KORE_INSTANCE_ID`, `KORE_PLAN_ORIGIN_PRINCIPAL` | the same values as in the server's `.env`; never change them later |
| `KORE_PLAN_ORIGIN_SECRET` | the same value as in the server's `.env` |
| `KORE_HERMES_CHILD_PATH` (optional) | the `PATH` the bridge gives Hermes. It **replaces** the default `/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin`, so keep the system folders in it. Needed when your model provider calls a program outside them, for example `/home/<user>/.local/bin:/usr/local/bin:/usr/bin:/bin`. Without it the chat fails with `hermes_failed`. `kore-doctor` checks it. |
| `KORE_COACH_NAME` (optional) | a display name for your coach, see [Coach name](#coach-name) |

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
TasksMax=512
MemoryMax=2G
LimitNOFILE=4096

[Install]
WantedBy=multi-user.target
```

The bridge **writes** the pairing state (`<HOME>/kore-server/state`), so that path must be in
`ReadWritePaths`. Without it, `ProtectSystem=strict` makes pairing fail: the bridge starts, logs
`Device pairing unavailable` and answers every pairing attempt with 401.

`TasksMax`, `MemoryMax` and `LimitNOFILE` cap the whole unit, the bridge (at most 64 connections)
and the Hermes runs it starts (at most 2 at once, each with Python and its MCP clients). If the
journal shows `fork failed`, `Too many open files` or an out-of-memory kill, raise the value. Then:

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now kore-coach-bridge
```

(If your state directory is not `<HOME>/kore-server/state`, set `KORE_STATE_DIR` in `bridge.env`
and `.env` to the same absolute path and use that path in `ReadWritePaths`; systemd does not expand
variables there.) If a firewall such as `ufw` is active, allow
only port 8090 from your local subnet. Docker bypasses `ufw` for its own ports, and the MCP port is
local anyway.

**7. Check everything.** Install `qrencode` first so that `kore-doctor pair` can show a QR code
(`sudo apt install qrencode`; without it you get the link only).

```bash
python3 ~/kore-server/scripts/kore-doctor
```

The exit code must be 0. See [kore-doctor](#checking-your-installation-kore-doctor). Then
[pair your iPhone](#pairing-your-iphone).

## Hermes in a Docker container (e.g. Unraid)

If your Hermes agent runs in the Hermes Docker image (all of its data under `/opt/data`), for example on
Unraid, the setup follows [Path B](#path-b-set-it-up-by-hand) with a few differences. There is no
ready-made container for the coach bridge: you run it as a process in the Hermes container. Below,
`<hermes>` stands for the name of your Hermes container and `<container-address>` for its fixed address.

| | Hermes on the host (Path B) | Hermes in a container |
| --- | --- | --- |
| Ingest and MCP | Docker Compose on the host | the same, on the Docker host |
| MCP address in the profile | `http://127.0.0.1:8000/mcp/` | `http://mcp:8000/mcp/`, inside the Compose network |
| Hermes data | `<home>/.hermes` | `/opt/data`, told to the bridge by `KORE_HERMES_DATA_DIR` |
| Coach bridge | systemd service | a process in the Hermes container, on a fixed container address |
| Restart the bridge | `sudo systemctl restart kore-coach-bridge` | start the process again (step 5) |

**1. The code, and two folders for the Hermes container.** Get the code on the Docker host as in step 1 of
Path B. Then mount the checkout read-only into the Hermes container (for example at `/opt/kore-server`)
and one folder that the container user may write (for example at `/opt/kore-bridge`). The second one will
hold `bridge.env` and the pairing state `state/`. The checkout contains your server
`.env`; if the Hermes container should not see it, mount a copy of the checkout without `.env` (the bridge
and the doctor need `coach-bridge/`, `scripts/`, `hermes/` and `contract/`). Create the writable folder on
the host, owned by the user Hermes runs as in the container (`docker exec <hermes> id`; the image uses
`hermes`), mode 0750, then create the pairing state **before** the first `docker compose up`, as that
user:

```bash
docker exec --user hermes <hermes> python3 /opt/kore-server/scripts/kore-doctor init-state \
  --state-dir /opt/kore-bridge/state
```

It prints the `KORE_STATE_GID=` line for the server `.env`.

**2. Ingest and MCP.** Do steps 3 and 4 of Path B on the Docker host. In the server `.env`, besides
`KORE_STATE_GID`: `KORE_STATE_DIR` set to the pairing state's path **on the host** (Compose mounts it
read-only into the ingest), optionally `KORE_INGEST_PORT` and `KORE_MCP_PORT` if a host port is taken
(see step 3), and

```
KORE_MCP_URL=http://mcp:8000/mcp/
```

Compose ignores `KORE_MCP_URL`; only `kore-doctor` reads it. The MCP is then not on the loopback of the
machine the doctor runs on, so the doctor skips its check on `127.0.0.1` with a `[HINWEIS]` line instead
of failing and leaves the way to the MCP to `hermes mcp test kore`. The MCP stays published on the host's
`127.0.0.1` only.

**3. Put the Hermes container into the Compose network.** From inside the Hermes container, `127.0.0.1`
is the container itself, so the MCP is reached by its Compose service name instead:

```bash
docker network ls                                  # the Kore project network, e.g. kore-server_default
docker network connect kore-server_default <hermes>
```

Every container on that network can reach the MCP port (the token is still required), so keep the
network to the two Kore containers and Hermes. Docker does not keep the connection when the Hermes
container is re-created: repeat the command, or declare the network in the Hermes container's own
definition.

**4. The coach profile.** Do step 5 of Path B inside the Hermes container (`docker exec -it --user hermes
<hermes> hermes profile create kore-coach` and so on; the profile lives in
`/opt/data/profiles/kore-coach`), with two differences: the MCP `url` is `http://mcp:8000/mcp/`, and
`MCP_KORE_API_KEY` goes into `/opt/data/profiles/kore-coach/.env`. The same choice applies: a new coach
profile (recommended) or an existing one with the consequences listed there. Check with `docker exec
--user hermes <hermes> hermes -p kore-coach mcp test kore`: *Connected*, *Tools discovered: 10*.

**5. The bridge.**

- *Address.* The bridge listens on one concrete address, never `0.0.0.0`, and `127.0.0.1` is not
  reachable from your iPhone. Give the Hermes container a fixed address: either an address of its own in
  your home network (on Unraid, a custom network such as `br0` with a fixed IP), which your iPhone then
  reaches directly, or a fixed address on a user-defined Docker network with the port published on the
  host (`-p 10.0.0.10:8090:8090`); in that case pass `--bridge-url http://10.0.0.10:8090` to
  `kore-doctor pair`. Only loopback, RFC 1918 and `100.64.0.0/10` addresses are accepted. An address that
  changes after a restart makes the bridge fail with "Cannot assign requested address".
- *`bridge.env`* in `/opt/kore-bridge` (`chmod 600`), as in step 6 of Path B, with these values:
  `KORE_BRIDGE_BIND_HOST=<container-address>`; `KORE_HERMES_BIN=` the path of `hermes` in the container
  (`docker exec --user hermes <hermes> sh -c 'command -v hermes'`); `KORE_HERMES_HOME=` the home of the
  Hermes user in the container (the image: `/opt/data`); `KORE_HERMES_DATA_DIR=/opt/data`; and
  `KORE_STATE_DIR=/opt/kore-bridge/state`, the pairing state's path **in the container**. Write the
  values without quotes: Docker's `--env-file` (below) passes them literally.
- *Start, and restart.* Without systemd, the bridge is a process you start in the container as the Hermes
  user (not as root), with the host's copy of `bridge.env`:

  ```bash
  docker exec --user hermes <hermes> pkill -f coach-bridge/bridge.py     # only to restart: stops the old one
  docker exec -d --user hermes --env-file /host/path/to/kore-bridge/bridge.env <hermes> \
    sh -c 'exec python3 /opt/kore-server/coach-bridge/bridge.py >> /opt/data/logs/kore-bridge.log 2>&1'
  ```

  The first line fails if the image has no `pkill`; restarting the Hermes container stops every process
  in it as well. The process is gone after the Hermes container restarts or is re-created: run the start
  command again, for instance from a script your host runs after the container starts. The bridge can
  also run in a container of its own that carries the same Hermes installation and mounts the same
  `/opt/data`; a restart is then `docker restart` of that container, and it must not start a second
  Hermes gateway.
- *Check.* `docker exec --user hermes <hermes> python3 /opt/kore-server/scripts/kore-doctor
  --bridge-env /opt/kore-bridge/bridge.env --bridge-only` checks the bridge and Hermes. Check the ingest
  and the MCP from the host with the `curl` lines of step 4 of Path B (with your ports). If the Hermes
  container can see the server `.env`, leave out `--bridge-only`: the doctor then also compares the
  shared secrets and checks the ingest.

Then [pair your iPhone](#pairing-your-iphone) as usual. Two things differ from Path B security-wise: the
bridge, Hermes and everything else in that container run as the same user, so anything in the container
with file or terminal tools can read `bridge.env` (see [Network](#network), "The trust boundary is the
Linux user"); and the MCP is reachable from every container on the Compose network.

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

- The code has 20 characters (100 bits), is valid for **10 minutes** and works **once**: a successful
  pairing uses it up. A request that does not carry a well-formed code at all (wrong length or
  characters, no string, no JSON) does not touch the open code. After **1000 failed attempts** with a
  well-formed code, that code is dead and you create a new one. The limit is high on purpose: 100 bits
  cannot be guessed in 10 minutes anyway, and a low limit would let anyone in your network kill your
  open code with a few wrong requests. Creating a new code also replaces the open one. From one
  address the server accepts at most 5 failed attempts per minute and then answers 429; the check and
  the count are one step, so parallel requests cannot slip past the limit.
- The link never contains a long-lived token. A device token is 256 bits of randomness; the server
  stores only its SHA-256 hash, in the state directory, and logs neither codes nor tokens.
- The device token is valid for the bridge and the ingest API, nothing else. The MCP server accepts
  only its own token, which devices never see.
- A link that someone intercepts is useless outside your home network or VPN (the server is not
  reachable there), works only once and expires after 10 minutes.
- **Pair on a network you trust.** The bridge speaks plain HTTP, so the code and the new device token
  cross the network unencrypted at that moment. Pair at home. On the road, pair only through your
  router's VPN or through the Tailscale HTTPS names (see [VPN](#remote-access-via-vpn)), never over
  plain HTTP in a network you do not control.

Useful options: `--name "My iPhone"` labels the device that pairs next; `--bridge-url` and
`--ingest-url` set the addresses if they cannot be derived from `bridge.env` and `.env`. Accepted are
private IPv4 addresses (10/8, 172.16/12, 192.168/16; `http` or `https`), `https://<name>.ts.net` and
`https` to a Tailscale address (100.64/10) (see [VPN](#remote-access-via-vpn)); plain `http` to Tailscale
addresses is refused because iOS blocks it. Never loopback, other host names or public addresses.

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

## Coach name

Your coach can have a name that Kore shows in the chat. Set it in `bridge.env` and restart the
bridge:

```
KORE_COACH_NAME=Coach Lena
```

The name is trimmed and must be 1 to 24 characters: letters (any language), digits, space, hyphen,
apostrophe and period, nothing else. An invalid value is **ignored** with one warning in the journal
at startup; the bridge still starts and the app uses its default name. `GET /health` and
`GET /capabilities` **of the bridge** carry the name as `"coach_name": "Coach Lena"` only when it is valid; without a
valid name the field is missing. There is no capability flag for it: the field's presence is the
signal. The name changes only what the app displays, not the coach's personality (that is the
profile's `SOUL.md`).

## Remote access via VPN

At home Kore reaches the server directly over your home network. Away from home, use a **VPN into
your home network**. The rule: **never forward a port, never bind to `0.0.0.0`, never use a public
tunnel service** (reverse tunnels, Tailscale Funnel and similar). All three services speak plain
HTTP on your own network; inside a VPN tunnel that traffic is encrypted by the tunnel, at home it is
not. The one exception is Tailscale, which needs HTTPS (option 2).

The services listen on **one** address each, so pick the address your iPhone should always use.

### Option 1: WireGuard on your router (for example FRITZ!Box)

Many routers can act as a WireGuard server. Create a connection for your iPhone in the router's VPN
settings, import the configuration into the WireGuard app on the iPhone, and make sure the tunnel
routes your home subnet (the `AllowedIPs` of the iPhone's configuration include it, for example
`10.0.0.0/24`). The phone then reaches the server under the **same address as at home**
(`10.0.0.10`). Nothing changes on the server: keep `KORE_BIND_ADDRESS` and `KORE_BRIDGE_BIND_HOST` on
the LAN address (with a DHCP reservation) and pair as usual. The router's VPN port is the VPN's own
endpoint, not a forwarded Kore port. Because the phone gets a home-network address, plain `http` works
exactly as it does at home.

### Option 2: Tailscale with `tailscale serve` (HTTPS)

iOS refuses unencrypted `http` to Tailscale addresses (`100.64.0.0/10`) and to `*.ts.net` names (App
Transport Security). So the Tailscale address itself cannot be used the way the home address can. The
way that works: let `tailscale serve` publish the services inside your tailnet with a certificate.
Kore then talks `https://<name>.ts.net`. `tailscale serve` forwards to **the address the service
listens on**, so there are two consistent setups:

- **Recommended: keep the services on your LAN address** (`KORE_BIND_ADDRESS` and
  `KORE_BRIDGE_BIND_HOST` as in the setup, for example `10.0.0.10`). Home network and a router VPN
  keep working with plain `http`, and `tailscale serve` forwards to `http://10.0.0.10:8081` and
  `http://10.0.0.10:8090`.
- **Tailscale only:** set both bind addresses to `127.0.0.1` and let `tailscale serve` forward to
  `http://127.0.0.1:8081` and `http://127.0.0.1:8090`. Then nothing listens on a network address, but
  the iPhone can no longer reach the server by its home address, only through the `ts.net` names.

1. **Install Tailscale on the server** following Tailscale's instructions for your distribution, and
   sign in (`sudo tailscale up`).
2. **Install the Tailscale app on your iPhone** and sign in with the same account.
3. **Choose an unremarkable machine name** (in the admin console, or
   `sudo tailscale set --hostname=<name>`). HTTPS certificates put the machine name and your tailnet
   name into public certificate transparency logs that anyone can search. So not
   `<your-name>-health-server`, but something neutral. Enable HTTPS certificates in the admin console.
4. **Decide on the bind address.** Recommended: change nothing, the services keep listening on the
   LAN address. For Tailscale only: in the server's `.env` `KORE_BIND_ADDRESS=127.0.0.1`, in
   `bridge.env` `KORE_BRIDGE_BIND_HOST=127.0.0.1`, then `docker compose up -d` and
   `sudo systemctl restart kore-coach-bridge`.
5. **Publish them in the tailnet.** The target is the address from step 4 (`10.0.0.10` here; use
   `127.0.0.1` for Tailscale only):

   ```bash
   sudo tailscale serve --bg --https=443  http://10.0.0.10:8081    # ingest
   sudo tailscale serve --bg --https=8443 http://10.0.0.10:8090    # bridge
   ```

   If you moved the ingest port (`KORE_INGEST_PORT`), use it in the first command. Check
   `tailscale serve --help` for the flags of your version. Never use `tailscale funnel`; it
   publishes to the open internet.
6. **Pair with the HTTPS names.** Pass them explicitly: `kore-doctor pair` would otherwise build
   `http` links from the bind addresses, which iOS does not accept for Tailscale.

   ```bash
   python3 ~/kore-server/scripts/kore-doctor pair \
     --bridge-url https://<machine>.<tailnet>.ts.net:8443 \
     --ingest-url https://<machine>.<tailnet>.ts.net
   ```

   Switch Tailscale on on the phone, then open the link. `kore-doctor pair` refuses plain `http` to
   `100.x` addresses and `*.ts.net` names and tells you so.
7. **Keep your tailnet personal** and its access rules tight: everything that can reach these names
   can try your tokens.

With the Tailscale app on, the same names work at home and away.

## Security

### Principles

These points are not recommendations. They are the assumptions the code was written under. If you
violate them, you are running something other than the system described here.

1. **Only the condensed snapshot, never raw health data.** The app sends daily values: recovery,
   one HRV value per day (SDNN in ms, also as a history over the last days), resting heart rate,
   sleep hours, workout summaries and, since 0.14, daily step totals and the day's last weight and
   body fat. Nutrition (daily totals of energy, protein, carbohydrates and fat) travels only if you
   switch on its own sharing option in Kore and allow Kore to read it in Health; switching it off
   makes the next sync clear it on the server. All of it goes only to your own server and your own
   coach. No beat-to-beat intervals, no measurement series within a day, no HealthKit exports. The MCP tools pass on exactly these values and cannot hand out anything else,
   because nothing else is there.
2. **Writing only as a plan proposal, confirmed in the app.** The only writing tool is
   `propose_training_plan`. It stores the plan with `status=pending`. The app fetches it, shows it
   to you, and only your tap on "Accept" makes it effective. An agent cannot silently activate
   anything in Kore. Every write also needs a short-lived, HMAC-signed one-time ticket from the
   coach bridge (`KORE_PLAN_ORIGIN_SECRET`); the MCP token alone is not enough.
3. **No port forwarding.** All ports belong on your home network, a VPN address or loopback (loopback is the choice if you use Tailscale only, with `tailscale serve` forwarding to it). The
   bridge, the ingest API and the MCP server refuse to listen on `0.0.0.0` and accept only a
   concrete IPv4 loopback, RFC 1918 or VPN (100.64.0.0/10) address (in Docker the check applies to
   the address Compose publishes the port on). The MCP port is published on `127.0.0.1` only and is
   not reachable from your network at all.
4. **Separate tokens, long enough, and you rotate them.** Ingest, bridge and MCP each have their own
   static token of at least 32 characters, so a compromised agent cannot write what the app may
   write; services refuse to start with a shorter one. Under Docker each container also gets only
   its own keys (see [Services and containers](#services-and-containers)). See
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
   Every prompt also begins with a fixed first line, `[KORE-APP-NACHRICHT]`: in single-query mode
   Hermes reads a file path at the very start of a message (`/…`, `~/…`, `./…`, `file://…`, `C:/…`)
   as an attachment, and an image file would go to the vision model, past the tool list. With the
   fixed line first, the text of a message never stands at the start.
7. **Tool poisoning.** An MCP client decides what to do from tool *descriptions*. If you register a
   malicious MCP server in the same profile that sees `kore`, it can hide instructions in its tool
   descriptions. In the profile that sees `kore`, use only tool servers whose source you know. This
   server cannot solve that for you; it can only keep small what there is to fetch (point 1).
8. **Pairing is single-use, short and revocable.** Codes are random, valid 10 minutes, usable once,
   dead after 1000 failed attempts and rate-limited per address. Device tokens are random, stored only
   as hashes and can be revoked at any time. Neither codes nor tokens are logged.
9. **Authentication comes before the body.** The ingest API checks the bearer token in a layer in
   front of the web framework, before it reads a single byte of the request: without a valid token the
   answer is 401 at once, even for a 16 MiB upload. Only the exact path `/health` is open (and may
   receive at most 4 KiB); every other path, unknown ones included, needs the token first. Exactly one
   `Authorization` header counts; a request with two is rejected, on the coach bridge and the MCP
   server too. `/docs`, `/redoc` and `/openapi.json` do not exist.
10. **No web search where the health context is, unless you ask for it.** A health chat (and the
    adjustment of today's workout, which carries health context too) gets the tools
    `skills,memory,kore`, never `web`, so text smuggled into such a chat has no tool to send the health
    context to an address of its choosing. Only when the person switches web search on for the health
    chat in the app (request field `health_web_allowed: true`, off by default) does that chat get
    `web,skills,memory,kore`; the adjustment of today's workout never does. A general chat has web
    search but gets neither health context nor the `kore` MCP. Two paths remain, see
    [Residual risks](#residual-risks): the opt-in itself, and an indirect, two-step one through the
    profile's memory. The protocol is in
    [`contract/health-web-opt-in-v1.md`](contract/health-web-opt-in-v1.md).

### Network

- **No port forwarding. Ever.** What you would expose is a health history with a single bearer token
  in front of it. Away from home, use a [VPN](#remote-access-via-vpn).
- **Plain HTTP.** All three services speak HTTP without TLS. Anyone who can read your network sees
  tokens and content. Use it only in a network you trust; guests and strange devices belong on a
  separate Wi-Fi. A VPN tunnel encrypts the leg through the internet. This applies to
  [pairing](#pairing-your-iphone) as well: pair at home, and on the road only through the router VPN
  or the Tailscale HTTPS names.
- **Docker before version 28.** Up to Docker 27, a port published on `127.0.0.1` could be reached
  from other machines in the same layer-2 segment, for example the same Wi-Fi (moby issue #45610).
  For the MCP port that would defeat "this machine only". Use Docker 28 or newer, or add a rule to
  the host firewall (nftables) that drops traffic to a port published on `127.0.0.1` unless it
  arrives on the loopback interface. The `kore-doctor` check "MCP not reachable via the network
  address" does not rule this case out.
- **Leave `KORE_MCP_ALLOWED_ORIGINS` empty** unless you really put your own web interface in front.
- **Secrets are stored in plain text.** `.env`, `bridge.env` and the profile's `.env` are `chmod 600`,
  owned by the service user, never in a backup that ends up elsewhere, never in a git repo, a
  screenshot or a chat window. `.gitignore` keeps `*.env`, databases, `*.bak*` and key files out of
  git, `.dockerignore` out of the image.
- **The trust boundary is the Linux user.** File permissions do not separate two programs that run as
  the same user. Anything of that user that has terminal or file tools can read `bridge.env`, the
  profile's `.env`, the server `.env` and the pairing state: another Hermes profile, a messenger
  gateway, a cron job. The app chat has no such tools, but other surfaces of the same Linux user may.
  Best: run the bridge and the coach profile under a Linux user of their own, with no other agent. At
  least use a dedicated Hermes profile for Kore (it is a separation of memory and configuration, not
  of files) and know what your other profiles of that user can do.
- **Supply chain.** Dependencies install only from hash-locked files (`--require-hashes`), the base
  image is pinned by digest, GitHub Actions are pinned to commit SHAs and a gitleaks job scans the
  history for secrets.

### Services and containers

- **Docker Compose.** There is no `env_file`: each container gets only the keys it needs, as listed
  in `docker-compose.yml`. Ingest holds neither the MCP token nor the origin secret (it only reads
  the principal), the MCP server holds no ingest token and no state directory. Both containers run
  with `read_only: true`, `/tmp` as a small `noexec` tmpfs, all capabilities dropped,
  `no-new-privileges`, `pids_limit: 128` and a memory limit (768m ingest, 384m MCP; a 16 MiB upload
  needs roughly 400 to 500 MiB while it is parsed). Only the volume `kore-db:/data` is writable. The
  ingest container has a health check.
- **Where they listen.** The Docker image listens on every interface inside the container (that is
  the only way the port mapping reaches it); the exposure is the host address Compose publishes the
  port on. Compose hands that address to the container as `KORE_PUBLISHED_BIND`, and the process
  checks it by the same rule as the bridge and refuses to start otherwise. `KORE_PUBLISHED_BIND`
  belongs to `docker-compose.yml`, not to `.env`. Without Docker the services check their own listen address:
  the ingest API reads `KORE_INGEST_HOST`, otherwise `KORE_BIND_ADDRESS`, the MCP server reads
  `KORE_MCP_HOST`; an unset or empty value means `127.0.0.1`, and wildcards are refused.
- **systemd.** The units in [`deploy/`](deploy/) run sandboxed (`ProtectSystem=strict`,
  `PrivateTmp`, kernel and control-group protection, restricted address families and namespaces,
  `NoNewPrivileges`, emptied capability set); the bridge unit above has a lighter sandbox of the
  same kind. All three carry `MemoryMax`, `TasksMax` and `LimitNOFILE`. The ingest API starts
  through `python ingest_api.py`, which checks the address; a hand-written unit that starts
  `uvicorn` directly skips that check. Under systemd both server units read the same
  `.env` as the same user, so the key split of Compose does not exist there; it would not help
  against a process of the same user anyway.

### Rotating secrets

| Secret | Where | Who else needs it |
| --- | --- | --- |
| `KORE_INGEST_TOKEN` | server `.env` | Kore, only on the expert path |
| `KORE_MCP_TOKEN` | server `.env` | the coach profile's `.env` as `MCP_KORE_API_KEY` (and that of any other profile you gave read access) |
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
| allow `web,skills,memory` in the general chat; `skills,memory,kore` in a health chat with an origin ticket; `skills,memory` where health context has no ticket (adjusting today's workout); `web,skills,memory,kore` (without ticket `web,skills,memory`) in a health chat only when the request says `health_web_allowed: true` | allow `web` near health context without that explicit request, give `web` to the adjustment of today's workout in any case, or pass service secrets on to Hermes: the child gets only `HOME`, `PATH`, seven keys such as `LANG`/`TZ` and, if you set `KORE_HERMES_DATA_DIR`, `HERMES_HOME` |
| start every prompt with the fixed first line `[KORE-APP-NACHRICHT]` and run every Hermes run in a fresh, empty temporary directory that is removed afterwards | let a message that begins with a file path attach that file, or let Hermes load `AGENTS.md`, `CLAUDE.md` and similar context files from the home directory into the prompt |
| return the adjusted workout of the day only if it is lighter or shorter than the one the app sent, with nothing but `session_id`, `name` and `exercises` at its top level | return a heavier or longer one (more sets, weight, repetitions, longer duration, new or renamed exercises): the reply is dropped and the app gets a 502 with a fixed sentence |
| read sessions of the sources `kore-general`/`kore-health` to find titles and predecessors, and delete them on request or after 30 days, pinned and archived ones included; the source of every session is checked before it is deleted, when forgetting a whole scope and on expiry as well | touch sessions of other sources, or change the profile's memory or configuration |
| write one `kore-turn-failed` line (class, code, exit) to the journal when a turn fails | write prompts, answers, tokens, health values or Hermes stderr to the journal or to the app |
| run at most 2 Hermes runs at once, whichever route they come by (a background turn, a synchronous chat, the adjustment of today's workout; a third gets 429 `busy`), and keep 64 answers in memory for up to 24 hours | start without token (at least 32 characters), origin secret and principal, start or run Hermes with `skills.inline_shell` on (checked at startup and before every run), listen on a public address, or gain privileges through `sudo`/setuid (`NoNewPrivileges=yes`) |

Fixed limits: a request is at most 256 KiB, a prompt 120 KiB, Hermes output 512 KiB, run time 150 s;
at most 64 connections at once (further ones are closed without an answer), 20 s from the start of a
connection to read headers and body (on top of the 30 s socket timeout, so a slow sender cannot hold
a thread open); the ingest API accepts at most 16 MiB, and only with a valid token. Ingest and MCP
answer every further request with 503 once 1024 connections are open or 1024 requests are running, and
close an idle keep-alive connection after 5 s (uvicorn has no deadline for headers or body, see
[Residual risks](#residual-risks)). Tokens are compared in constant time in all three services; the
MCP server compares SHA-256 digests of equal length, so not even the token's length shows in the
timing.

### Residual risks

Kore sends health context only in chats with health sharing turned on, and the bridge hands out the
`kore` MCP only there. What your bot does with it afterwards, Kore does not control:

- **Your model provider sees everything in the prompt**, in a health chat that is the health
  context. You choose the provider in the profile.
- **Memory.** The bot can note something from a health chat with `memory`. Such notes flow into every
  later session of the profile, including general app chats and other channels of the same profile
  (for example Telegram). Deleting or expiry in Kore does not reach them; you manage this memory in
  Hermes.
- **Memory is a two-step path from health chats to web search.** `memory` stays among the tools of a
  health chat on purpose, so the coach can remember what it needs for the next chat. That means a
  health chat can write health facts into the profile's memory, and a general chat, which has web
  search, reads the same memory. In theory, a manipulated web page could therefore get a general chat
  to send out health facts remembered earlier: first the memory is filled in a health chat, then a web
  page steers a later general chat. The health chat itself has no `web` unless you switch it on (next
  point). What helps: keep sensitive
  values out of what the coach stores (ask it not to remember them), look through the profile's
  memory now and then and delete what you do not want kept, and use a dedicated profile for Kore so
  that nothing from your other channels mixes in. The health and the general chat of Kore share that
  profile, so a dedicated profile does not separate them from each other.
- **Web search in the health chat, if you switch it on.** It is your explicit choice in the app, at
  your own risk, and off by default. With it on, the health context in the prompt and the `kore` tools
  sit next to a tool that can fetch addresses: a manipulated web page that the coach finds or reads
  could talk it into sending health values to an address of its choosing, and the bridge cannot stop
  that. Switching it on in the middle of a chat exposes more than the current message: the run with
  web search continues the same session, so the earlier turns of that health chat, with the health
  context they carried, are in front of the coach as well. Start a new chat for a question that needs
  the web if the earlier ones should stay out of it. As a soft mitigation, not a barrier, the bridge
  appends one fixed sentence to the end of the prompt of such a turn
  (`[KORE-WEBSUCHE — BRIDGE-AUTORITATIV]`: do not put health values, names, notes or plan contents
  into search queries or URLs, search in general terms only); a manipulated text can talk the coach
  out of it. Switch it on only for a question that needs it, and off again
  afterwards. The adjustment of today's workout never has web search, whatever the app sends (it
  answers `400` to the field).
- **External memory provider.** If the profile uses one, `memory` brings its tools along, and turns
  including the health context may go there. Check with `hermes -p <profile> memory status`.
- **Skills.** `skill_manage` belongs to `skills`. The bot can create or change skills of the profile,
  also when a text from a web search talks it into it. Such a skill keeps working in all channels of
  the profile. Look at new or changed skills if something seems strange. Shell commands inside
  skills (`skills.inline_shell`) must stay off, see step 5 of the setup. The bridge checks this at
  startup and again before every Hermes run, but the check covers neither Hermes' managed
  configuration overlay nor the older backup that Hermes falls back to when `config.yaml` is broken.
  After changes to the profile, run `kore-doctor`.
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
- **Nothing brakes guessing a bearer token.** Only the pairing code has an attempt limit. The static
  and device tokens are protected by their length (at least 32 characters, random) and by the fact
  that the services are reachable only in your home network or VPN.
- **The connection limit is global, not per address.** Someone in your network can keep the bridge's
  64 connections busy for up to 20 s each. That blocks the bridge for the time being but binds no
  unlimited threads or memory.
- **Ingest and MCP have no deadline for headers or body.** uvicorn 0.54, which serves both, offers
  none. Both answer every further request with 503 once 1024 connections are open or 1024 requests are
  running, and close an idle keep-alive connection after 5 s. But a sender in your network that
  trickles its headers keeps such a connection open as long as it likes, and 1024 of them block the
  ingest API (the MCP server listens on loopback only) until they close. Memory per connection stays
  small; the systemd units also cap open files at 4096.
- **Authenticated uploads still cost memory.** With a valid token, a 16 MiB snapshot needs roughly
  400 to 500 MiB while the ingest API parses it; the memory limit of the container or unit caps a
  runaway. Without a valid token, nothing is read.
- **Other surfaces of the same Linux user** can read the secret files, see
  [Network](#network).

## Data and privacy

Your data stays on **your** server. Kore sends your health data only to the server you pair, and
this repository contains no analytics, telemetry or outbound calls of its own. FastMCP's own check
for a newer release on PyPI at startup is switched off (`FASTMCP_CHECK_FOR_UPDATES=off`, set by
`mcp_server.py` itself and in the Compose file and the systemd unit). What each part stores:

| Where | What |
| --- | --- |
| **Ingest + MCP**, Docker volume `kore-db` (SQLite) | the latest snapshot from the app: profile basics, recovery, HRV daily values up to 30 days, resting heart rate, sleep, workouts with heart rate and calories, daily steps, weight and body fat (up to 30 days), nutrition daily totals (up to 30 days; only if you share them in Kore), training plans with names and notes; pending plan proposals |
| **Pairing state**, `state/` next to `docker-compose.yml` | the server id, the hash of an open one-time code with its count of failed attempts, and for each paired device an id, a name, the hash of its token and two timestamps. No tokens or codes in clear text |
| **Coach bridge** | no database. Running turns live in memory (up to 24 hours, 64 answers) and are lost on restart. In the state directory, `bridge-sessions.json` lists the Hermes sessions the bridge started, each with its scope and last use (no content, no chat ids), so it can delete them; `bridge-hermes-options.json` remembers which options the installed Hermes rejects. Each Hermes run works in an empty folder (`kore-bridge-work/chat` in the Hermes data folder) that is emptied before and after the run. The journal gets a one-line failure record without content |
| **Hermes profile**, `~/.hermes/profiles/<profile>/` | the chat sessions of Kore (since Hermes v0.22.5 with the gateway's source `oneshot`, before that `kore-general`/`kore-health`) and the profile's memory. The bridge deletes Kore sessions after 30 days without activity and when you delete a chat in Kore; it never touches memory |
| **Your model provider** | the prompts, and with "Share health context with coach" turned on, the health context in each message. That is how a chat works; choose your provider accordingly |

Without the "Share health context" switch, the coach receives only what you type. Steps, weight and
body fat use the Health access Kore already has; nutrition needs a switch of its own in Kore's
experimental features (off by default, shown once a coach is connected) and its own Health
permission, and switching it off clears it on your server with the next sync. The ingest does not log snapshot
values or echo them in errors.

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
| Kore's **Test coach bridge** says the server is older than the app | The ingest does not name a snapshot field the app knows: `GET /capabilities` answers 404 (an ingest v0.13.0) or lacks `activity_body_nutrition_v1` or `apple_hrv_v1`. Nothing is broken: Kore keeps syncing and leaves out what the server cannot take (steps, weight, nutrition, Apple's recovery HRV labels). | [Upgrade](#upgrading) the server to the version Kore names. |
| "Your coach bridge is missing: …" | The bridge does not report a capability Kore requires; the name is in the text. | Update kore-server. If `plan_origin_v1` is missing, also check `KORE_PLAN_ORIGIN_SECRET` and `KORE_PLAN_ORIGIN_PRINCIPAL` in `bridge.env`; both must be set. If `device_pairing_v1` is missing, the state directory is unusable (see the two pairing rows below). |
| Pairing fails (401 `pairing_failed`) | The code is wrong, expired (10 minutes), already used, dead after 1000 failed attempts, or pairing is unavailable on the server. | Run `kore-doctor pair` again for a fresh link. If it keeps failing: `journalctl -u kore-coach-bridge` and look for `Device pairing unavailable`. |
| Pairing keeps failing with 401, although the link is fresh | 1000 failed attempts with a well-formed code kill that code; something in your network kept trying it. A request that is not a well-formed code does not count. | Create a new code with `kore-doctor pair` (a dead code does not come back) and look for the device that keeps sending: the journal names the address of every failed attempt. |
| Pairing: 429 `rate_limited` | Five failed attempts within a minute from one address. | Wait a minute and use a new code. |
| Journal: `Device pairing unavailable: …` | The state directory is missing or the bridge cannot write to it. | `kore-doctor init-state`, then check ownership. With the hardened unit, `<HOME>/kore-server/state` must be in `ReadWritePaths`. |
| `docker compose up` stops with "set KORE_STATE_GID …" | `KORE_STATE_GID` is missing in `.env`. | Run `kore-doctor init-state` and copy the printed line into `.env`. |
| `docker compose up` stops with "set KORE_… in .env" for another key | Compose needs that key for a container and finds none. | Add the key to `.env` (compare with `.env.example`). |
| `docker compose up` stops with "port is already allocated" (8081 or 8000) | Another program, or a second Kore stack, already uses that host port. | Set `KORE_INGEST_PORT` and/or `KORE_MCP_PORT` in `.env` to free ports, `docker compose up -d`; use the new port in Kore's server address (pairing links follow it) and in the profile's MCP `url`. |
| `hermes mcp test kore` fails with `No module named 'mcp'` | Your Hermes was installed without its MCP extra; the cause is on the Hermes side. | `hermes pm install --extra mcp` as the user (or in the container) that runs Hermes; `kore-doctor` names the same remedy. |
| A service does not start and the message says "is too short: at least 32 characters are required" (ingest, MCP) or "KORE_BRIDGE_TOKEN must contain at least 32 characters" (bridge) | Since 0.13 the three bearer tokens need 32 characters. | Generate a new one with `openssl rand -hex 24`, enter it, restart, and update the other side (Hermes profile `.env`, or Kore on the expert path). |
| The ingest container restarts in a loop; the log says "… must be a concrete IPv4 loopback, RFC 1918 or VPN (100.64.0.0/10) address" | `KORE_BIND_ADDRESS` is `0.0.0.0`, a host name or a public address. | `docker compose logs ingest`; put the LAN address of this machine into `KORE_BIND_ADDRESS`, then `docker compose up -d`. The same message from `python ingest_api.py` or `python mcp_server.py` without Docker names `KORE_INGEST_HOST`, `KORE_BIND_ADDRESS` or `KORE_MCP_HOST`. |
| The bridge does not start and names `skills.inline_shell` | The profile's `config.yaml` has shell commands in skills on, or not clearly off; a quoted, escaped or flow-style key anywhere in the file counts as not clearly off. | Under `skills:` set exactly `inline_shell: false` (or remove the key) and use plain keys only, see step 5 of the setup, and restart. |
| Coach calls fail with 503; the journal says `kore-profile-unsafe skills.inline_shell …` | The profile's `config.yaml` was changed while the bridge runs and is no longer clearly safe. The bridge starts no Hermes run until it is. | Fix the file as in the row above; the next call works without a restart. `kore-doctor` shows the verdict. |
| Kore shows "Die Bridge ist gerade ausgelastet." (429 `busy`) | Two Hermes runs are already going (a chat, a background turn or an adjusted workout); the bridge takes no third. A stopped turn holds its place until Hermes has really ended. | Wait a moment and try again; a run ends after 150 s at the latest. |
| Adjusting today's workout shows "Die Anpassung des Coachs steigert die Session; erlaubt ist nur Reduzieren." (502 `escalation`) | The coach's reply would make the session heavier or longer, or add or rename exercises. The bridge drops it. | Nothing is broken; try again. If it keeps happening, look at the instructions in the profile's `SOUL.md`. |
| Journal of the bridge: `fork failed`, `Too many open files` or an out-of-memory kill | The unit's `TasksMax`, `LimitNOFILE` or `MemoryMax` is too low for your Hermes runs. | Raise the value in `kore-coach-bridge.service`, `sudo systemctl daemon-reload`, restart. |
| Paired, but the ingest API answers 401 | Ingest cannot read `devices.json`: the group does not match. | `kore-doctor` has a "state directory" check; make `KORE_STATE_GID` the directory's group and run `docker compose up -d --force-recreate`. |
| `kore-doctor pair` refuses an address | Accepted are private IPv4 addresses and `https://<name>.ts.net`, not `127.0.0.1`, other host names, `http` on a name or on a Tailscale (100.x) address, or public addresses. | Set `KORE_BRIDGE_BIND_HOST` and `KORE_BIND_ADDRESS` to the home-network address your iPhone uses, or pass `--bridge-url` and `--ingest-url` (for Tailscale: the `https://<name>.ts.net` names). |
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

`--bridge-env`, `--server-env` and `--state-dir` work before and after a sub-command
(`kore-doctor devices --state-dir …`) and without one.

It needs only the standard library and never prints a secret. It checks that

- the ingest `/health` returns 200 (and 403 with a foreign `Origin`), on `KORE_INGEST_PORT` from the
  server `.env` (default 8081);
- the MCP server on `127.0.0.1` (port `KORE_MCP_PORT`, default 8000) returns 401 without a token and is
  not reachable via the network address. If `KORE_MCP_URL` is set in the server `.env` (an MCP that lives
  only in the Compose network), the doctor skips this with a `[HINWEIS]` line;
- the bridge returns 401 without a token, and its `/health` with a token reports every capability in
  [`contract/app-required-capabilities.json`](contract/app-required-capabilities.json),
  `prompt_transport: stdin` and `app_chat_toolsets` without terminal, file, code, cron, computer and
  browser tools and, in the health scope, without `web` (the opt-in list `health_with_web` may add
  nothing but `web`);
- `KORE_BRIDGE_TOKEN` (`bridge.env`) and `KORE_INGEST_TOKEN` and `KORE_MCP_TOKEN` (server `.env`)
  have at least 32 characters;
- `KORE_PLAN_ORIGIN_SECRET` (compared by hash), `KORE_INSTANCE_ID` and `KORE_PLAN_ORIGIN_PRINCIPAL` are
  equal in server and bridge;
- the profile exists (under `KORE_HERMES_DATA_DIR` if you set it), `hermes -p <profile> mcp test kore`
  succeeds (30 second limit; if it fails because Hermes lacks the `mcp` module, the doctor names
  `hermes pm install --extra mcp`), Hermes runs with exactly the environment the bridge gives it, and
  the profile's `SOUL.md` contains the current [`kore-app-chat-soul.md`](hermes/kore-app-chat-soul.md);
- `skills.inline_shell` is off in the profile's `config.yaml` (missing or `false`, plain YAML keys
  only);
- it names the profile's model provider and its enabled plugins; a program that a provider plugin
  cannot find on the bridge's `PATH` shows up as Hermes' own start warning and is a deviation;
- the pairing state directory exists with the right permissions and `KORE_STATE_GID` is its group.

Exit code 0 only without any deviation. `--bridge-only` leaves out ingest, MCP and the server `.env`,
for example when they run on another machine. The other sub-commands are `init-state`, `pair`,
`devices` and `revoke` (see [Pairing](#pairing-your-iphone)). Most messages are in German; the
pairing hint is bilingual.

## Upgrading

```bash
cd ~/kore-server
git fetch --tags origin
git checkout --detach <new tag>       # the tag Kore or the release page names
git rev-parse HEAD                    # must equal the commit hash named for that tag; if not, stop here
docker compose up -d --build
sudo systemctl restart kore-coach-bridge      # bridge under systemd; in a Docker container see below
python3 scripts/kore-doctor
hermes -p <profile> mcp test kore             # "Connected", and the tools of this version
```

1. **Check out the tag and verify it.** Release tags are never moved. Compare `git rev-parse HEAD` with
   the commit hash that Kore's setup prompt or the release page names for that tag. A different hash
   means do not continue.
2. **Rebuild** ingest and MCP with `docker compose up -d --build`.
3. **Restart the bridge.** Under systemd: `sudo systemctl restart kore-coach-bridge`. If it runs as a
   process in the Hermes container (see
   [Hermes in a Docker container](#hermes-in-a-docker-container-eg-unraid), step 5): stop it and start it
   again with the same command.
4. **Check.** `kore-doctor` must exit with 0, and `hermes -p <profile> mcp test kore` must say *Connected*
   (with version 0.14 or newer, *Tools discovered: 10*).

Paired devices stay paired: the pairing state and all tokens are untouched. New variables in `.env` and
`bridge.env` are optional; add one only if you need what it does (`KORE_INGEST_PORT`, `KORE_MCP_PORT`,
`KORE_MCP_URL` in `.env`, `KORE_HERMES_DATA_DIR`, `KORE_HERMES_WORK_DIR` in `bridge.env`). An older server keeps working with a
newer Kore, which leaves out what the server cannot take and says so under **Test coach bridge**, with a
link to this section and a text you can paste into your Hermes to run these steps.

Run `kore-doctor` (and `hermes -p <profile> mcp test kore`) again after every `hermes update`, too.
Release notes, with an "Upgrading from …" section per version, are in [CHANGELOG.md](CHANGELOG.md).

**Security hardening in 0.13.** Services that used to start can now refuse to: tokens under 32
characters, `skills.inline_shell` switched on, a bind address outside loopback, private and VPN
ranges. Compose passes `.env` keys on only when `docker-compose.yml` lists them, and a self-written
systemd unit for the ingest API must start `python ingest_api.py` instead of `uvicorn`. Copy the
bridge unit from this README again (new limits). The list is under "Breaking for self-hosters" in
the [CHANGELOG](CHANGELOG.md).

**Coming from 0.10 or earlier:** run `python3 scripts/kore-doctor init-state` and add the printed
`KORE_STATE_GID=…` to `.env` (Compose refuses to start without it); add the state directory to
`ReadWritePaths` if you use the hardened unit; replace the `## Kore-App-Chat` section in the
profile's `SOUL.md` with the current file (`kore-doctor` flags an outdated one). The MCP server is
now published on `127.0.0.1` only.

## Reference

### The tools (9 read, 1 proposes)

| Tool | Returns |
| --- | --- |
| `get_recovery_today` | Today's values: recovery, HRV (with the method actually used), resting heart rate, sleep hours, band |
| `get_hrv_trend(days=30)` | One HRV value per day (SDNN in ms) for the last `days` days: daily values, no series within a day |
| `get_sleep_summary(days=7)` | Per night: total, deep and REM hours and efficiency |
| `get_recent_workouts(limit=10)` | Date, type, duration, average heart rate, volume |
| `get_activity_body_trend(days=30)` | Per day (1 to 30 days, newest last): steps (`count`) and body (`weight_kg` in kg, `body_fat_pct` in %, the day's last measurement) |
| `get_nutrition_trend(days=30)` | Per day (1 to 30 days, newest last): `kcal`, `protein_g`, `carbs_g`, `fat_g` as daily totals from Apple Health; incomplete when not everything is logged; empty unless you share nutrition in Kore |
| `get_active_plan` | The training plan currently active in the app |
| `get_training_plans` | All plans, plus the timestamp of the snapshot |
| `get_coach_briefing` | The condensed daily picture in one call: recovery, HRV, sleep, workouts, profile, plans, and the last 7 days of steps and weight (`activity_body_7d`) and of nutrition (`nutrition_7d`) |
| `propose_training_plan(plan, origin_ticket)` | Stores a plan as `pending`; the app confirms |

The HRV method is part of the key name: `hrv_rmssd_ms`, `hrv_sdnn_ms`, or `hrv_ms` if the method is
unknown. A value is never silently labelled as SDNN. Explicitly **not** delivered, because it is not
on the server at all: raw health values, location, contacts, photos, cycle details. Your coach cannot
delete, tick off or silently activate anything.

Steps, weight, body fat and nutrition are new in snapshot v2 (Kore 0.14, server 0.14). They are
read-only for your coach, reach only your own server, and a list the app does not send stays as stored.
Nutrition arrives only while you share it in Kore. The field-by-field contract is in
[`contract/snapshot-v2.md`](contract/snapshot-v2.md).

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
`GET /capabilities`, `GET /health` (no token). `GET /capabilities` answers
`{"snapshot_capabilities": ["activity_body_nutrition_v1", "apple_hrv_v1"]}`: Kore reads it before each
sync and sends the newer snapshot fields (steps, weight, nutrition; Apple's recovery HRV labels) only
to an ingest that names them (an ingest v0.13.0 answers 404, and Kore syncs without them); see
[`contract/snapshot-v2.md`](contract/snapshot-v2.md). (The coach bridge has a `GET /capabilities` of its
own, see below.) Everything else needs `Authorization: Bearer <token>`, where the token is
`KORE_INGEST_TOKEN` or a paired device's token. The token is checked before the body is read: a
missing or wrong token (or two `Authorization` headers) gets `401` with `Connection: close` and no
body is parsed. Only the exact path `/health` is open (it may receive at most 4 KiB); every other
path, unknown ones and variants such as `/ingest%0A` included, gets `401` without a token. The API
has no `/docs`, `/redoc` or `/openapi.json`. The MCP server, too, refuses a request with two
`Authorization` headers with `401` before reading its body.

### Coach bridge protocol

The bridge is plain Python without dependencies and calls the `hermes` CLI as a bounded child process
(`hermes -p <profile> chat -Q --format stream-json --query-file - --source kore-<scope> -t <tools>
--run-budget <s> [--resume <id>]`) in an empty temporary working directory, with a prompt whose first
line is always `[KORE-APP-NACHRICHT]`. It is deliberately Hermes-specific, tested against Hermes Agent
v0.21.4 and v0.21.5. It reads the stream-json from stdout; answer and session come from the `result`
record, never from stderr. The fixtures in `coach-bridge/fixtures/hermes-v0.21.4/` come from real
runs, with the model name and the memory provider replaced by placeholders; after a Hermes update
run `test_bridge.py` and `test_runner_stream_json.py`.

If you write your own bridge for another agent, this is the small protocol the app speaks:

- `GET /health`, `GET /capabilities`: capability list, `api_version: 2`, and the optional
  `"coach_name"` (a string of 1 to 24 characters, present only if `KORE_COACH_NAME` is valid).
- `POST /pair` (no bearer) and `POST /unpair` (device token): see
  [`contract/pairing-v1.md`](contract/pairing-v1.md).
- `POST /coach` with
  `{message, conversation_id, session?, context?, health_context_allowed, health_web_allowed?}`.
  `conversation_id` is a canonical lower-case UUID. `session` is a bridge-signed reference
  `general:<id>.<sig>` or `health:<id>.<sig>`, bound to scope and chat; if it does not match or
  Hermes no longer knows the session, the context starts anew (`context: "reset"`) instead of an
  error. Health context is passed on only if `health_context_allowed` is literally `true`
  (fail-closed). The optional `health_web_allowed` (capability `health_web_opt_in_v1`, details in
  [`contract/health-web-opt-in-v1.md`](contract/health-web-opt-in-v1.md)) must be a JSON boolean, else
  `400`. Only `true` in the health scope adds `web` to the tools of that turn and the fixed
  `[KORE-WEBSUCHE …]` sentence to the end of its prompt; in the general scope it is ignored, and
  switching it between the turns of one chat keeps the session.
- With an additional `client_message_id` (canonical UUID), `POST /coach` starts a turn job and answers
  at once `202 {turn_id, status}`; the same `client_message_id` in the same chat yields the same turn.
  A turn already running in the chat: `409`. Hermes runs are limited to two at once for the whole
  bridge, whichever route they come by; a third gets `429 {"error": …, "code": "busy"}`, also on a
  synchronous `POST /coach` (without `client_message_id`) and on `POST /coach/adjust-today`.
- `GET /coach/turns/{turn_id}/events`: server-sent events with `id:`; `Last-Event-ID` replays later
  events, `: keepalive` every 15 s. Only text deltas `{"delta": …}`, and at the end
  `{"done": true, "used_kore": …, "context": …, "session"?: …, "title"?: …}` or
  `{"error": …, "code": …}`; tool calls stay inside the bridge.
- `GET /coach/turns/{turn_id}` returns state or result (a finished turn is deleted with this answer);
  `POST /coach/turns/{turn_id}/cancel` ends Hermes, idempotent. The buffer lives in memory only, at
  most 24 hours.
- `POST /coach/adjust-today`: JSON answer with the adjusted workout of the day. It never gets web
  search: a request that carries `health_web_allowed` at all is `400`. Only reducing is
  allowed: if the coach's reply has more sets, more weight, more repetitions or a longer duration, a
  new or renamed exercise, or another `session_id`, nothing of it is returned and the answer is
  `502 {"error": "Die Anpassung des Coachs steigert die Session; erlaubt ist nur Reduzieren.", "code": "escalation"}`.
  Omitting exercises or sets, lighter weights, fewer repetitions and a changed note are fine. The
  returned `session` carries only `session_id`, `name` and `exercises` at its top level; anything else
  the coach puts there is dropped.
- `POST /coach/forget` with `{conversation_id, sessions: [<signed reference>, …]}` or
  `{scope: "health" | "all"}`: stops running turns, deletes their buffers and then the Hermes
  sessions including compression predecessors. Reply `{deleted, not_found, failed}`; deletion is
  confirmed only with `failed: 0`. Before a session is deleted, its source is read: only a session of
  the source `kore-<scope>` is deleted, anything else counts as `failed`. Pinned and archived sessions
  are included (`sessions prune --dry-run --include-archived --include-pinned`). An open session that
  Hermes hides from its list (archived or hidden) cannot be found by its id; forgetting a scope counts
  it as `failed` instead of confirming. With very many sessions the 120 s budget can end first; the
  rest counts as `failed` and expiry catches up.
- Expiry: Kore sessions without activity for `KORE_BRIDGE_SESSION_RETENTION_DAYS` days (default and
  maximum 30), pinned and archived ones included, are deleted at startup and daily, never while a turn
  runs, and with the same source check.
- Without a valid `Authorization: Bearer` (static bridge token or device token), or with two
  `Authorization` headers: 401, before any of the body is read. An unfinished
  installation is an error, never open access: if `KORE_BRIDGE_TOKEN`, `KORE_PLAN_ORIGIN_SECRET` or
  `KORE_PLAN_ORIGIN_PRINCIPAL` is empty or still `change-me…`, the bridge does not start and names the
  key (never the value). It also does not start with a `KORE_BRIDGE_TOKEN` shorter than 32 characters
  or with `skills.inline_shell` on or not clearly off in the profile's `config.yaml` (the message
  names the setting and the file). The same check runs before every Hermes run: if the file has become
  unsafe, a coach call gets `503 {"error": …, "code": "profile_unsafe"}` (a turn start before its
  `202`; a background turn already accepted ends with `hermes_failed`).
- Tools per `-t` list instead of `--safe-mode` (`/health` reports them as `app_chat_toolsets`):
  general `web,skills,memory`; health `skills,memory,kore`, without `web`. Health context without an
  origin ticket (adjusting today's workout) gets `skills,memory`: no kore MCP, no `web`. Only on
  `health_web_allowed: true` does a health chat get `web,skills,memory,kore` (`web,skills,memory`
  without ticket); `/health` reports that list as `app_chat_toolsets.health_with_web`. Never
  terminal, files, code execution, cron jobs, computer or browser control.
- Limits of the HTTP server itself: at most 64 connections at once (further ones are closed without
  an answer) and 20 s from the start of a connection to read headers and body.

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
