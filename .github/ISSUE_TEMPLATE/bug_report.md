---
name: Bug report
about: Something does not work as documented
title: ""
labels: bug
---

> **Do not post tokens, one-time codes, pairing links or health data** — not in the text, not in logs,
> not in screenshots. Replace them with `<token>`, `<code>`, `<LAN-IP>`. A security problem does not
> belong here: use "Report a vulnerability" under the Security tab instead (see SECURITY.md).

**What happened, and what did you expect?**

**Steps to reproduce**

1.
2.

**Setup**

- kore-server version (tag or commit):
- Kore app version:
- Hermes Agent version (`hermes --version`):
- Docker / Compose version, or systemd (without Docker):
- Reached over: home network / VPN (WireGuard, Tailscale, …)
- Pairing (link/QR) or static tokens:

**Output of `python3 scripts/kore-doctor`** (it prints no secrets; look it over before pasting)

```
```

**Relevant log lines** (`docker compose logs ingest`, `journalctl -u kore-coach-bridge`), with
secrets and personal data removed

```
```
