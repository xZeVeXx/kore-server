# Sicherheit / Security

## Eine Schwachstelle melden

Bitte **kein öffentliches Issue**. Melde Schwachstellen privat über die GitHub Security
Advisories dieses Repos: Reiter **Security** → **Report a vulnerability**. Ein anderer Kontakt ist
nicht nötig.

- Beschreibe Version (Tag oder Commit), Aufbau (Docker Compose oder systemd, Hermes-Version) und
  die Schritte bis zum Befund.
- **Keine echten Tokens, Geheimnisse oder Gesundheitsdaten** in die Meldung. Nimm Platzhalter
  (`<token>`, `<LAN-IP>`) oder erzeugte Testwerte.
- Das ist ein Freizeitprojekt ohne Bug-Bounty. Ich antworte nach bestem Bemühen, in der Regel
  innerhalb von 14 Tagen, und veröffentliche einen Hinweis erst, wenn es einen Fix gibt.

## Was dazugehört

| Im Rahmen | Nicht im Rahmen |
| --- | --- |
| `server/` (Ingest-API, MCP-Server), `coach-bridge/`, `scripts/kore-doctor`, `deploy/`, `Dockerfile`, `docker-compose.yml`, die Beispielkonfigurationen | Hermes Agent selbst, Modell-Anbieter, MCP-Server Dritter im selben Profil |
| Umgehen der Token- oder Origin-Prüfung, des Einmal-Tickets für Plan-Vorschläge, der Werkzeug-Allowlist der Bridge oder der Bind-Regeln | Betrieb gegen die Anleitung, etwa Ports ins Internet freigegeben oder `0.0.0.0` erzwungen |
| Geheimnisse oder Gesundheitsdaten in Logs, Fehlermeldungen, Prozessliste oder Antworten | Ein bereits kompromittierter Rechner oder Root im Heimnetz |

## Unterstützte Versionen

Nur der jeweils neueste Tag (`v0.10.x`). Fixes kommen als neuer Tag; ältere Stände bekommen keine
Nachbesserung.

## Annahmen, unter denen der Code sicher ist

Heimnetz oder VPN, nie das offene Internet; Klartext-HTTP nur in einem Netz, dem du vertraust;
eigene, zufällig erzeugte Tokens, die du rotierst. Die ausführliche Fassung samt der bekannten
Restrisiken steht im [README](README.md#sicherheit).

---

**English:** Please report vulnerabilities privately via this repository's GitHub Security
Advisories (**Security → Report a vulnerability**), never as a public issue, and never include
real tokens or health data. Only the latest tag is supported. This is a hobby project without a
bug bounty; responses are best effort, usually within 14 days.
