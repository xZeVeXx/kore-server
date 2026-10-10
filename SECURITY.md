# Security / Sicherheit

*English first, [Deutsch weiter unten](#sicherheit-deutsch).*

## Reporting a vulnerability

Please **do not open a public issue**. Report vulnerabilities privately through GitHub's
**Private Vulnerability Reporting** for this repository: tab **Security** → **Report a
vulnerability**. No other contact is needed.

- Describe the version (tag or commit), your setup (Docker Compose or systemd, Hermes version) and
  the steps that lead to the finding.
- **No real tokens, secrets or health data** in the report. Use placeholders (`<token>`,
  `<LAN-IP>`) or generated test values.
- This is a hobby project without a bug bounty. Responses are best effort, usually within 14 days,
  and an advisory is published only once a fix exists.

## Scope

| In scope | Out of scope |
| --- | --- |
| `server/` (ingest API, MCP server), `coach-bridge/`, `scripts/kore-doctor`, `deploy/`, `Dockerfile`, `docker-compose.yml`, the example configurations | Hermes Agent itself, model providers, third-party MCP servers in the same profile |
| Bypassing the token or origin checks, the pairing (one-time code, rate limit, device tokens, revocation), the one-time ticket for plan proposals, the bridge's tool allowlist or the bind rules | Operating against the documentation, for example ports forwarded to the internet or `0.0.0.0` forced |
| Secrets, device tokens or health data in logs, error messages, the process list or responses | An already compromised machine, or root on your home network |

## Supported versions

Only the latest tag (currently `v0.13.x`). Fixes ship as a new tag; older versions get no backports.

## Assumptions under which the code is safe

Home network or VPN, never the open internet; plain HTTP only inside a network you trust; your own
randomly generated tokens, which you rotate. The long version, including the known residual risks,
is in the [README](README.md#security).

---

## Sicherheit (Deutsch)

### Eine Schwachstelle melden

Bitte **kein öffentliches Issue**. Melde Schwachstellen privat über das **Private Vulnerability
Reporting** von GitHub für dieses Repo: Reiter **Security** → **Report a vulnerability**. Ein anderer
Kontakt ist nicht nötig.

- Beschreibe Version (Tag oder Commit), Aufbau (Docker Compose oder systemd, Hermes-Version) und die
  Schritte bis zum Befund.
- **Keine echten Tokens, Geheimnisse oder Gesundheitsdaten** in die Meldung. Nimm Platzhalter
  (`<token>`, `<LAN-IP>`) oder erzeugte Testwerte.
- Das ist ein Freizeitprojekt ohne Bug-Bounty. Antworten kommen nach bestem Bemühen, in der Regel
  innerhalb von 14 Tagen, und ein Hinweis wird erst veröffentlicht, wenn es einen Fix gibt.

### Was dazugehört

| Im Rahmen | Nicht im Rahmen |
| --- | --- |
| `server/` (Ingest-API, MCP-Server), `coach-bridge/`, `scripts/kore-doctor`, `deploy/`, `Dockerfile`, `docker-compose.yml`, die Beispielkonfigurationen | Hermes Agent selbst, Modell-Anbieter, MCP-Server Dritter im selben Profil |
| Umgehen der Token- oder Origin-Prüfung, der Kopplung (Einmalcode, Bremse, Gerätetokens, Widerruf), des Einmal-Tickets für Plan-Vorschläge, der Werkzeug-Allowlist der Bridge oder der Bind-Regeln | Betrieb gegen die Anleitung, etwa Ports ins Internet freigegeben oder `0.0.0.0` erzwungen |
| Geheimnisse, Gerätetokens oder Gesundheitsdaten in Logs, Fehlermeldungen, Prozessliste oder Antworten | Ein bereits kompromittierter Rechner oder Root im Heimnetz |

### Unterstützte Versionen

Nur der jeweils neueste Tag (derzeit `v0.13.x`). Fixes kommen als neuer Tag; ältere Stände bekommen
keine Nachbesserung.

### Annahmen, unter denen der Code sicher ist

Heimnetz oder VPN, nie das offene Internet; Klartext-HTTP nur in einem Netz, dem du vertraust; eigene,
zufällig erzeugte Tokens, die du rotierst. Die ausführliche Fassung samt der bekannten Restrisiken
steht in der [README.de.md](README.de.md#sicherheit).
