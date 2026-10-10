# Device pairing v1

Capability: `device_pairing_v1` (bridge `/capabilities` and `/health`). The bridge reports it only
while its state directory is usable. The app needs it for pairing only; the static-token setup
(expert path) works without it. Both endpoints need a bearer, so a client sees the capability
only after pairing or with the static token.

A one-time code, shown by the server operator as a link or QR code, is exchanged for a
per-device token. The token is the device's credential for **both** the coach bridge and the
ingest API. The server keeps only its SHA-256.

## State directory

`KORE_STATE_DIR` on the host. Default for the bridge and the CLI: `state/` in the repository root,
i.e. `./state` next to `docker-compose.yml`, which mounts the same directory read-only into the
ingest container at `/state`. If the bridge runs from a copy outside the repository, set
`KORE_STATE_DIR` to the same absolute path in `bridge.env` and in `.env`.

Permissions: directory `0750`, `devices.json` `0640`, `pairing.json`, `server_id` and `.lock` `0600`.
Owner and group are those of the process that creates the files (the bridge user or the CLI run
as that user); nothing is ever `chown`ed. Every write is atomic (temporary file + `rename`, final
mode set before the rename). Writers (bridge, CLI) serialise on an exclusive lock on `.lock` in
the same directory. The ingest API only reads.

The ingest process runs as a different user (Docker: uid 10001 without capabilities; systemd:
`User=kore`) and reaches `devices.json` only through the group. `devices.json` holds nothing but
SHA-256 hashes of 256-bit random tokens, which cannot be used as tokens; the one-time code hash
(`pairing.json`) and the server id stay private to the bridge user.

- Docker Compose: `KORE_STATE_GID` in `.env` is the gid of the bridge user (`id -g`);
  `docker-compose.yml` adds it to the ingest container with `group_add`. Compose refuses to start
  without it. The directory must exist **before** `docker compose up`, otherwise Docker creates it
  as root: `scripts/kore-doctor init-state` creates it (`0750`, with `server_id`) and prints the gid.
- systemd: put `User=kore` into the bridge user's group (`usermod -aG <group> kore`).
- `scripts/kore-doctor` checks that the directory exists with these modes and that
  `KORE_STATE_GID` is the directory's group.

| File | Content |
|---|---|
| `pairing.json` | `{"code_sha256": "<hex>", "expires_at": <unix>, "attempts": <int>}` — at most one open code; optional `"device_name"`: label chosen by the operator (`kore-doctor pair --name`), used instead of the name the app sends |
| `devices.json` | `{"devices": [{"id": "<8 hex>", "name": "<str ≤ 40>", "token_sha256": "<hex>", "created_at": <unix>, "last_seen": <unix\|null>}]}` |
| `server_id` | 8 hex characters, created once at random (the app shows "Server a1b2c3d4") |

## One-time code

- 20 characters, RFC 4648 Base32 alphabet `A–Z 2–7`, no padding (100 bit), drawn with `secrets`.
- Valid for 600 s. Creating a new code replaces any open one.
- Consumed by the **first** redeem attempt, whether it succeeds or fails.
- Compared case-insensitively after trimming whitespace (the stored hash is of the upper-case form).
- At most 5 failed attempts per minute per client IP address; further attempts get `429` and do not
  touch the open code.

## Link

```
kore://pair?v=1&bridge=<URL>&ingest=<URL>&code=<CODE>&sid=<server_id>
```

URLs percent-encoded. Allowed: `http` or `https` with a private IPv4 address (10/8, 172.16/12,
192.168/16; also what a router VPN such as WireGuard hands the phone), `https` with a CGNAT/Tailscale
IPv4 address (100.64/10), or `https` with a Tailscale name (`https://<name>.ts.net[:port]`,
`tailscale serve`). Plain `http` to 100.64/10 and to `*.ts.net` is refused because iOS App Transport
Security blocks it. Other host names, public addresses and loopback are refused. The QR code contains exactly
this link. It never contains a long-lived token.

## `POST /pair` (bridge, no bearer)

Request body (at most 4096 bytes):

```json
{"code": "<CODE>", "device_name": "<str>"}
```

`device_name` is reduced to printable characters, trimmed and cut to 40 characters; empty or
missing becomes `Device`.

| Status | Body |
|---|---|
| 200 | `{"token": "<43 chars base64url, 256 bit>", "device_id": "<8 hex>", "server_id": "<8 hex>", "scopes": ["coach", "ingest"]}` |
| 401 | `{"error": "pairing_failed"}` — wrong, expired, used or missing code, malformed body, or pairing unavailable on this server |
| 429 | `{"error": "rate_limited"}` |
| 403 | request carries an `Origin` header (browsers are never allowed; the app sends none) |
| 413 | body larger than 4096 bytes |

The token appears in plain text only in this response.

## `POST /unpair` (bridge, bearer = device token)

`Authorization: Bearer <device token>` → `204`, the device is removed and its token stops working
at once on the bridge and the ingest API. The static token or an unknown token → `401`.

## Authentication on all other routes

Bridge and ingest accept the static token (`KORE_BRIDGE_TOKEN` / `KORE_INGEST_TOKEN`, the expert
setup) **or** a device token. A device token is checked by SHA-256 against every entry of
`devices.json` with `hmac.compare_digest`, without early exit. Only the bridge updates
`last_seen` (at most once per minute per device). The MCP server accepts only its own static
token; devices never talk to it.

## Revocation

`kore-doctor devices` lists `id`, `name`, `created_at`, `last_seen` (never hashes);
`kore-doctor revoke <id>` removes the entry. Bridge and ingest re-read `devices.json` whenever it
was replaced, so revocation applies to the next request.

## Logging

Neither the code, the token nor their hashes are ever logged. The bridge logs (logger
`kore.pairing`) new codes with their expiry, paired and revoked device ids, and failed or
rate-limited attempts with the client address.
