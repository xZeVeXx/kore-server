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
| Bypassing the token or origin checks (including the token check that runs before the request body is read), the pairing (one-time code, attempt limits, device tokens, revocation), the one-time ticket for plan proposals, the bridge's tool allowlist (no `web` in the health scope unless the request carries `health_web_allowed: true`; never in the adjustment of today's workout), its "only reduce" check for adjusted workouts or the bind rules | Operating against the documentation, for example ports forwarded to the internet or `0.0.0.0` forced |
| Secrets, device tokens or health data in logs, error messages, the process list or responses | An already compromised machine, or root on your home network |

## Supported versions

Only the latest tag (currently `v0.13.x`). Fixes ship as a new tag; older versions get no backports.

## Assumptions under which the code is safe

Home network or VPN, never the open internet; plain HTTP only inside a network you trust; your own
randomly generated tokens of at least 32 characters, which you rotate; the bridge and the coach
profile run as a Linux user of their own, because file permissions do not separate programs of one
user. The long version is in the [README](README.md#security).

## Known residual risks

Documented in the README under [Residual risks](README.md#residual-risks) and [Network](README.md#network).
A report that only restates one of them is not a new finding; a way to make one worse, or a fix, is
welcome.

- The profile's memory connects health chats and general chats: a health chat can store facts there,
  and a general chat, which has web search, reads them (a two-step path, health chats themselves have
  no `web` unless the person switches it on).
- Web search in the health chat is an explicit opt-in (`health_web_allowed`, off by default, at the
  person's own risk): a manipulated page could talk the coach into sending health values to an
  address of its choosing. Getting `web` into a health chat or into the adjustment of today's workout
  *without* that request is a finding.
- Another program of the same Linux user with terminal or file tools can read the secret files.
- Bearer tokens have no attempt limit (only the pairing code has one); their protection is a length of
  at least 32 characters and a network you control.
- The bridge limits connections globally (64), not per address; a valid token can still upload a
  16 MiB snapshot, which needs roughly 400 to 500 MiB while it is parsed.
- Plain HTTP inside the trusted network, pairing included.
- Docker before version 28 let other machines in the same layer-2 segment reach ports published on
  `127.0.0.1` (moby #45610).

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
| Umgehen der Token- oder Origin-Prüfung (auch der Token-Prüfung, die vor dem Lesen des Bodys läuft), der Kopplung (Einmalcode, Versuchsgrenzen, Gerätetokens, Widerruf), des Einmal-Tickets für Plan-Vorschläge, der Werkzeug-Allowlist der Bridge (kein `web` im Health-Scope, außer die Anfrage trägt `health_web_allowed: true`; nie bei der Tages-Anpassung), ihrer „Nur reduzieren“-Prüfung für angepasste Trainings oder der Bind-Regeln | Betrieb gegen die Anleitung, etwa Ports ins Internet freigegeben oder `0.0.0.0` erzwungen |
| Geheimnisse, Gerätetokens oder Gesundheitsdaten in Logs, Fehlermeldungen, Prozessliste oder Antworten | Ein bereits kompromittierter Rechner oder Root im Heimnetz |

### Unterstützte Versionen

Nur der jeweils neueste Tag (derzeit `v0.13.x`). Fixes kommen als neuer Tag; ältere Stände bekommen
keine Nachbesserung.

### Annahmen, unter denen der Code sicher ist

Heimnetz oder VPN, nie das offene Internet; Klartext-HTTP nur in einem Netz, dem du vertraust; eigene,
zufällig erzeugte Tokens mit mindestens 32 Zeichen, die du rotierst; Bridge und Coach-Profil laufen
unter einem eigenen Linux-Nutzer, denn Dateirechte trennen keine Programme desselben Nutzers. Die
ausführliche Fassung steht in der [README.de.md](README.de.md#sicherheit).

### Bekannte Restrisiken

Beschrieben in der README unter [Was bleibt (Restrisiken)](README.de.md#was-bleibt-restrisiken) und
[Netz](README.de.md#netz). Eine Meldung, die nur eines davon wiederholt, ist kein neuer Befund; ein Weg,
eines zu verschlimmern, oder ein Fix ist willkommen.

- Das Gedächtnis des Profils verbindet Health-Chats und allgemeine Chats: Ein Health-Chat kann dort
  Fakten ablegen, und ein allgemeiner Chat, der die Websuche hat, liest sie (ein Weg in zwei Schritten;
  die Health-Chats selbst haben kein `web`, solange die Person es nicht einschaltet).
- Die Websuche im Health-Chat ist ein ausdrücklicher Wunsch (`health_web_allowed`, standardmäßig aus,
  auf eigenes Risiko der Person): Eine manipulierte Seite könnte den Coach dazu bringen, Gesundheitswerte
  an eine Adresse ihrer Wahl zu schicken. `web` in einem Health-Chat oder in der Tages-Anpassung *ohne*
  diesen Wunsch ist dagegen ein Befund.
- Ein anderes Programm desselben Linux-Nutzers mit Terminal- oder Datei-Werkzeugen kann die
  Geheimnis-Dateien lesen.
- Bearer-Tokens haben keine Versuchsgrenze (nur der Kopplungscode hat eine); geschützt sind sie durch
  eine Länge von mindestens 32 Zeichen und ein Netz, das du kontrollierst.
- Die Bridge begrenzt Verbindungen global (64), nicht je Adresse; mit gültigem Token lässt sich weiter
  ein 16-MiB-Snapshot hochladen, der beim Parsen grob 400 bis 500 MiB braucht.
- Klartext-HTTP im vertrauenswürdigen Netz, auch bei der Kopplung.
- Docker vor Version 28 ließ andere Rechner im selben Layer-2-Segment auf `127.0.0.1` veröffentlichte
  Ports erreichen (moby #45610).
