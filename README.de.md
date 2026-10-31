# kore-server

*[English](README.md) · Deutsch*

`kore-server` ist das selbst betriebene Backend für **Kore**, eine iOS-App für Training und
Erholung. Damit stellst du Kore einen eigenen Coach zur Seite: einen KI-Agenten, den du selbst
betreibst (Hermes Agent), auf deiner eigenen Maschine, mit deinem eigenen Schlüssel eines
Modell-Anbieters. Die App schickt ein verdichtetes Tagesbild deiner Gesundheitsdaten an deinen
Server, dein Agent liest es, spricht in Kore mit dir und kann Trainingspläne **vorschlagen**, die du
in der App bestätigst.

Alles läuft auf deiner eigenen Hardware, in deinem eigenen Netz, unter deinen eigenen Tokens. Es gibt
keinen Dienst dahinter, den jemand anderes betreibt.

Zielgruppe sind Menschen, die zuhause ohnehin einen Agenten betreiben. Wer das nicht tut, braucht
dieses Repo nicht: Kore funktioniert ohne Server vollständig.

Sicherheitslücken bitte privat melden, siehe [SECURITY.md](SECURITY.md).

**Inhalt:** [Was es ist](#was-es-ist) · [Voraussetzungen](#voraussetzungen) · [Einrichtung](#einrichtung) ·
[Hermes im Docker-Container](#hermes-im-docker-container-z-b-unraid) · [Kopplung](#kopplung-mit-dem-iphone) ·
[Unterwegs per VPN](#unterwegs-per-vpn) ·
[Sicherheit](#sicherheit) · [Daten und Datenschutz](#daten-und-datenschutz) ·
[Wenn etwas nicht geht](#wenn-etwas-nicht-geht) · [kore-doctor](#installation-prüfen-kore-doctor) ·
[Aktualisieren](#aktualisieren) · [Referenz](#referenz) · [Lizenz](#lizenz)

---

## Was es ist

Drei kleine Dienste laufen auf einer Maschine, neben deinem Hermes-Agenten:

```
            iPhone (Kore)
              │         │
   Chat       │         │  Tageswerte
              ▼         ▼
      ┌──────────────┐  ┌────────────────┐
      │ Coach-Bridge │  │ Ingest-API     │   Docker Compose
      │ :8090        │  │ :8081          │   (Bridge: systemd)
      └──────┬───────┘  └───────┬────────┘
             │ startet          │ SQLite
             ▼                  ▼
      ┌──────────────┐  ┌────────────────┐
      │ Hermes Agent │─▶│ MCP-Server     │   nur 127.0.0.1
      └──────────────┘  │ :8000          │
        liest Kore-Daten└────────────────┘
        und schlägt Pläne vor
```

| Dienst | Port | Aufgabe | Erreichbar von |
| --- | --- | --- | --- |
| **Coach-Bridge** (`coach-bridge/`, reines Python, nur Standardbibliothek) | 8090 | Chat: Kore spricht mit der Bridge, die Bridge startet `hermes` für ein einziges, dafür vorgesehenes Profil und gibt die Antwort zurück. Erledigt auch die [Kopplung](#kopplung-mit-dem-iphone). Läuft als systemd-Dienst auf dem Host. | deiner Heimnetz-Adresse oder `127.0.0.1` hinter `tailscale serve` (`KORE_BRIDGE_BIND_HOST`) |
| **Ingest-API** (`server/`, FastAPI, in Docker) | 8081 | Nimmt die Tageswerte vom iPhone entgegen, nennt die Snapshot-Felder, die sie annimmt (`GET /capabilities`), und gibt offene Plan-Vorschläge zurück. | deiner Heimnetz-Adresse oder `127.0.0.1` hinter `tailscale serve` (`KORE_BIND_ADDRESS`) |
| **MCP-Server** (`server/`, FastMCP, in Docker) | 8000 | Lässt dein Hermes-Profil die Kore-Daten **lesen** (9 Werkzeuge) und einen Trainingsplan **vorschlagen** (1 Werkzeug). | **nur diesem Rechner** (`127.0.0.1`) |

Kore spricht mit der Bridge und der Ingest-API. Mit dem MCP-Server spricht nur Hermes.

Die Ports sind die Standardwerte in den Containern. Auf dem Host veröffentlicht Docker Compose Ingest und
MCP auf `KORE_INGEST_PORT` und `KORE_MCP_PORT` aus deiner `.env` (Standard 8081 und 8000); setze sie, wenn
ein Port belegt ist oder auf demselben Rechner ein zweiter Stapel läuft.

Zwei Helfer liegen im Repo: `scripts/kore-doctor` prüft die Installation und verwaltet die
Kopplung, `hermes/kore-app-chat-soul.md` ist der Abschnitt, den du in die `SOUL.md` deines Coaches
aufnimmst.

> **Was dein Coach sagt, ist keine Kore-Aussage.** Er ist eine fremde KI. Kore hat seine Sätze weder
> geschrieben noch nachgerechnet und prüft an einem Plan nur Form und Grenzen, nie den Inhalt.
> Weder Kore noch dein Coach stellen Diagnosen.

## Voraussetzungen

| | |
| --- | --- |
| **Kore** | Version 0.13 oder neuer für die Kopplung. Ältere Versionen (ab 0.10) funktionieren mit dem Einrichtungsweg per statischem Token (siehe [Kopplung](#kopplung-mit-dem-iphone)). Schritte, Gewicht und Ernährung für den Coach brauchen Kore 0.14 und diese Server-Version; ein älterer Server funktioniert mit einem neueren Kore weiter, das diese Werte dann weglässt. |
| **Ein Linux-Rechner, der läuft** | Homeserver, Mini-PC, NAS, Raspberry Pi 5 … im selben Netz wie dein iPhone. Docker mit Compose v2 für Ingest und MCP (empfohlen ab Docker 28, siehe [Netz](#netz)), Python 3 für die Bridge. |
| **Hermes Agent 0.21.4 oder neuer** | Installiert auf **demselben Rechner**: Die Bridge startet `hermes` als Kindprozess, und der MCP-Server ist nur lokal erreichbar. Getestet mit v0.21.4 und v0.21.5. Ohne Hermes funktionieren Lesen und Plan-Vorschläge über MCP weiter, nur zum Chatten fehlt der Partner. |
| **Dein eigener Schlüssel eines Modell-Anbieters** | Hermes braucht ein Modell. Bring deinen eigenen Anbieter-Schlüssel (oder ein lokales Modell) mit und gib ihn selbst in deinem Terminal ein, nie in einem Chat. |
| **Eine feste Adresse für den Server** | Eine DHCP-Reservierung im Router (die Dienste lauschen auf dieser Adresse; nur mit Tailscale geht stattdessen Loopback, siehe [VPN](#unterwegs-per-vpn)). |

Du brauchst **keine** öffentliche Adresse, **keine** Domain und **kein** HTTPS-Zertifikat. Alles
bleibt im Heimnetz oder in deinem VPN. Genau das ist der Punkt.

**Zu Modell-Anbietern.** Kore wählt keinen für dich. Nimm einen API-Schlüssel des Anbieters deiner
Wahl (oder ein lokales Modell): Die Kosten sind planbar und leicht zu sehen. Ein Chat-Abo (etwa ein
Claude-Abo) als Anbieter für deinen Agenten empfehlen wir **nicht**. Ob und wie ein Abo über einen
Agenten von Drittanbietern genutzt werden darf und wie das abgerechnet wird, ist unklar und kann sich
ändern. Lies die aktuellen Bedingungen deines Anbieters, bevor du dich darauf verlässt.

## Einrichtung

Such dir einen Weg aus. Beide enden am selben Punkt: drei laufende Dienste, geprüft mit `kore-doctor`.

### Weg A: Hermes richtet alles ein

Der kürzere Weg. Öffne in Kore **Einstellungen › Experimentelle Funktionen** und kopiere den
**Hermes-Einrichtungsprompt**. Gib ihn deinem **bestehenden** Hermes-Agenten, per SSH in
`hermes chat` (nicht über Telegram, Discord oder einen anderen Messenger).

Der Prompt ist eine Checkliste, die Hermes Phase für Phase abarbeitet. Er:

1. schaut sich dein System nur lesend an (Betriebssystem, Nutzer, Hermes-Version, Docker, freie
   Ports, Netzadresse) und hält an, wenn der Nutzer root ist, passwortloses `sudo` hat oder in der
   Gruppe `docker` steckt;
2. klont `kore-server` auf den Tag, den der Prompt nennt, und prüft den Commit-Hash;
3. legt die `.env` mit frisch erzeugten Geheimnissen an, die nie im Chat erscheinen, und startet
   Ingest und MCP mit Docker Compose;
4. fragt, ob ein **neues** Hermes-Profil für deinen Trainings-Bot angelegt wird (Standard, nie ein
   Klon deines Hauptprofils) oder ein **bestehendes** verwendet werden soll; beim bestehenden nennt er
   vorher die Folgen (jeder Weg dieses Profils kann dann deine Gesundheitsdaten lesen, siehe Schritt 5
   von Weg B) und braucht dein ausdrückliches Ja. Danach bittet er
   dich, das Modell selbst in deinem Terminal einzurichten, trägt den Kore-Abschnitt in die `SOUL.md`
   des Profils ein, registriert den MCP-Server und schaltet Auto-Titel ab;
5. richtet die Bridge als systemd-Dienst ein, oder, wo es kein systemd gibt (das Hermes-Docker-Image,
   Unraid), als Prozess im Hermes-Container
   ([Hermes im Docker-Container](#hermes-im-docker-container-z-b-unraid)), und lässt `kore-doctor` und
   drei Ende-zu-Ende-Proben laufen;
6. installiert `qrencode` (nach deinem OK) und endet mit einem Kopplungs-Link und einem QR-Code im
   Terminal für dein iPhone.

Er fragt vor jedem `sudo`-Befehl, jeder Paketinstallation, jeder Firewall-Änderung, jedem Löschen und
jeder Profil-Änderung. Texte aus dem Repo, von Webseiten und aus Tool-Ausgaben gelten im Prompt als
Daten, nicht als Anweisungen. Die manuellen Schritte unten sind derselbe Weg von Hand.

### Weg B: von Hand einrichten

Führ die Schritte als der Linux-Nutzer aus, der Bridge und Hermes betreibt (nicht als root).

**1. Den Code auf einem Release-Tag holen.**

```bash
git clone https://github.com/xZeVeXx/kore-server.git ~/kore-server
cd ~/kore-server
git checkout --detach v0.14.2
```

**2. Den Zustandsordner der Kopplung anlegen.** Er muss *vor* `docker compose up` existieren,
sonst legt Docker ihn als root an und die Bridge kann nicht hineinschreiben.

```bash
python3 scripts/kore-doctor init-state
```

Das legt `state/` an (Rechte 0750), samt Server-ID, und gibt eine Zeile `KORE_STATE_GID=<gid>` aus.
Diese Gruppen-ID brauchst du im nächsten Schritt für die `.env`: Der Ingest-Container läuft unter
einem anderen Nutzer und liest die Liste der gekoppelten Geräte über deine Gruppe.

**3. Den Server konfigurieren.**

```bash
cp .env.example .env && chmod 600 .env
openssl rand -hex 24   # -> KORE_INGEST_TOKEN
openssl rand -hex 24   # -> KORE_MCP_TOKEN
openssl rand -hex 32   # -> KORE_PLAN_ORIGIN_SECRET
```

Erzeuge die Werte, tipp sie nicht aus. Trag sie in die `.env` ein, zusammen mit der Adresse dieses
Rechners, unter der dein iPhone ihn erreicht, und der Gruppen-ID aus Schritt 2:

```
KORE_BIND_ADDRESS=10.0.0.10     # nie 0.0.0.0
KORE_STATE_GID=1000             # der Wert, den init-state ausgibt
```

Solange ein Token oder das Geheimnis leer ist oder noch mit `change-me` beginnt, starten Ingest und
MCP nicht; die Meldung nennt den Schlüssel, nie den Wert. Dasselbe gilt für ein Token mit weniger als
32 Zeichen (`openssl rand -hex 24` ergibt 48) und für eine `KORE_BIND_ADDRESS`, die keine konkrete
Loopback-, private (RFC 1918) oder VPN-Adresse (`100.64.0.0/10`) ist: Bei `0.0.0.0` oder einer
öffentlichen Adresse bricht der Ingest-Container mit einer Meldung ab und versucht es, weil er auf
Neustart steht, in einer Schleife immer wieder; den Grund zeigt `docker compose logs ingest`.
`KORE_MCP_ALLOWED_ORIGINS` bleibt leer. Compose startet außerdem nicht ohne `KORE_STATE_GID` und nicht
ohne einen anderen Schlüssel, den ein Container braucht (es sagt, welchen).

Compose liest die `.env` nur, um Werte einzusetzen: Die `docker-compose.yml` nennt je Container genau
die Schlüssel, die er bekommt (der Ingest sieht weder das MCP-Token noch das Origin-Geheimnis, der
MCP-Server nicht das Ingest-Token). Ein Schlüssel, den du selbst in die `.env` einträgst, erreicht
einen Container erst, wenn du ihn in der `environment:`-Liste des Dienstes in der
`docker-compose.yml` ergänzt.

*Verlegte Ports (optional).* Ist Port 8081 oder 8000 auf dem Host belegt oder läuft auf demselben
Rechner ein zweiter Stapel, ergänze `KORE_INGEST_PORT` und/oder `KORE_MCP_PORT` in der `.env`. Compose
veröffentlicht Ingest und MCP-Server dann auf diesen Host-Ports (die Container bleiben bei 8081 und
8000, der MCP bleibt auf `127.0.0.1`). `kore-doctor` und `kore-doctor pair` lesen beide Werte; nimm die
verlegten Ports in den `curl`-Zeilen unten, in der Server-Adresse in Kore und in der MCP-`url` des
Profils (Schritt 5).

**4. Ingest und MCP starten.**

```bash
docker compose up -d --build
curl -s 10.0.0.10:8081/health                                  # -> {"ok":true}
curl -s -o /dev/null -w '%{http_code}\n' 127.0.0.1:8000/mcp    # -> 401
```

Die **401** ist die richtige Antwort: Der MCP-Port weist jeden ab, der kein Token vorzeigt. Ohne
Docker siehe [`deploy/`](deploy/) (eigener Systemnutzer, venv, systemd-Units; dort lauscht die
Ingest-API auf `KORE_BIND_ADDRESS` und der MCP-Server auf `KORE_MCP_HOST`, Standard `127.0.0.1`).

**5. Das Hermes-Profil für deinen Coach wählen.** Kore spricht mit genau einem Profil: entweder einem
neuen Coach-Profil (empfohlen) oder einem, das du schon benutzt.

*Ein neues Coach-Profil (Standard, empfohlen).* Es startet leer: keine Erinnerungen, keine anderen
Tool-Server, nur das, was du ihm gibst. Solange du ihm keinen Messenger und keine geplante Aufgabe
hinzufügst, läuft darin nur der Kore-App-Chat, unter den Grenzen der Bridge (eine feste Werkzeugliste
je Chat).

```bash
HERMES=~/.hermes/hermes-agent/venv/bin/hermes     # oder wo `hermes` liegt
$HERMES profile create kore-coach                 # ohne --clone / --clone-all
$HERMES -p kore-coach model                       # Modell selbst einrichten, in deinem Terminal
$HERMES -p kore-coach config set auxiliary.title_generation.enabled false
```

Klon nie dein Hauptprofil: Das würde dessen API-Schlüssel, MCP-Server und Erinnerungen in den Coach
kopieren.

*Modell-Anbieter, die Hermes-Plugins sind.* Manche Anbieter (zum Beispiel ein Abo-Plugin) werden als
Plugin geliefert, und Plugins werden **pro Profil** eingeschaltet. Ein neues Profil hat sie noch nicht:
Installiere und aktiviere das Plugin im neuen Profil (`$HERMES -p kore-coach …`, so wie du es für dein
Hauptprofil getan hast) und melde dich beim Anbieter selbst in deinem Terminal an. Kopiere **keine**
Zugangsdaten-Dateien, `.env` oder Auth-Speicher aus einem anderen Profil. Startet das Plugin ein
Zusatzprogramm (etwa ein Anbieter-CLI in `~/.local/bin`), setze außerdem `KORE_HERMES_CHILD_PATH` in
Schritt 6, sonst scheitern Chats mit `hermes_failed`.

*Ein bestehendes Profil (deine Entscheidung, mit Folgen).* Du kannst `KORE_HERMES_PROFILE` auf ein
Profil setzen, das du schon benutzt, zum Beispiel eines mit einem Gedächtnis, das du behalten willst,
und die Ergänzungen unten darin vornehmen. Tu das nur, wenn du alles Folgende in Kauf nimmst:

- **Jeder Weg dieses Profils kann deine Gesundheitsdaten lesen.** Telegram und andere Messenger,
  Gruppenchats, die Desktop-App und geplante Aufgaben laufen im selben Profil. Ist der `kore`-MCP-Server
  darin eingetragen, kann jeder von ihnen die neun Lese-Werkzeuge aufrufen (Erholung, HRV, Schlaf,
  Workouts, Pläne, Schritte, Gewicht und Körperfett und, wenn du sie teilst, Ernährung), ohne über den
  App-Chat zu gehen. Die Grenzen der Coach-Bridge (feste Werkzeugliste, kein Terminal, keine Dateien,
  kein Code, keine Websuche neben Gesundheitskontext) gelten **nur** im App-Chat, nicht auf diesen
  anderen Wegen.
- **Gesundheitsdaten im allgemeinen Gedächtnis.** Kore-Chats laufen in diesem Profil. Ist der
  Gesundheitskontext erlaubt, kann der Coach Gelerntes im Gedächtnis des Profils ablegen, und jede
  spätere Sitzung des Profils (auch deine anderen Chats und Kanäle) kann es sehen.
- **Ein Kore-Abschnitt in dessen `SOUL.md`.** Der Kore-Abschnitt wird (nach einem Backup) in die
  Persönlichkeitsdatei des Profils geschrieben und bleibt Teil seiner Anweisungen für alles, was es tut.
- **Ein externer Gedächtnis-Anbieter (zum Beispiel Honcho) bekommt Kore-Chats**, Gesundheitskontext
  eingeschlossen, falls das Profil einen benutzt. Prüfen mit `$HERMES -p <profil> memory status`.
- **Löschen in Kore erreicht nicht alles.** Die Bridge löscht die Kore-*Sitzungen*; Kopien im Gedächtnis
  des Profils oder bei einem externen Gedächtnis-Anbieter bleiben, bis du sie dort entfernst.

Im Zweifel nimm ein neues Profil. Dann trägst du drei Dinge in das Profil ein.

*Den MCP-Server*, angehängt an `~/.hermes/profiles/kore-coach/config.yaml` (steht auch in
[`hermes/kore-coach-profile.example.yaml`](hermes/kore-coach-profile.example.yaml)):

```yaml
mcp_servers:
  kore:
    url: http://127.0.0.1:8000/mcp/     # nur lokal; der abschließende Schrägstrich gehört dazu
    headers:
      Authorization: Bearer ${MCP_KORE_API_KEY}
    enabled: true
    tools:
      resources: false
      prompts: false
    sampling:
      enabled: false
```

Das Token steht **nicht** in der `config.yaml`, sondern in der eigenen `.env` des Profils
(`~/.hermes/profiles/kore-coach/.env`, `chmod 600`) als `MCP_KORE_API_KEY=<dein KORE_MCP_TOKEN>`.
Nicht als systemd-`Environment=`: Die Bridge startet Hermes mit einer minimalen Umgebung und reicht
keine Dienst-Variablen weiter.

*Den Chat-Abschnitt der Persönlichkeit deines Coaches*,
[`hermes/kore-app-chat-soul.md`](hermes/kore-app-chat-soul.md). Steht in der `SOUL.md` schon ein
Abschnitt `## Kore-App-Chat`, **ersetzt** du ihn bis zur nächsten `## `-Überschrift, statt einen
zweiten anzuhängen: Ein alter Abschnitt mit veraltetem Plan-Schema sorgt dafür, dass die App
Vorschläge still verwirft. Dieser Befehl macht beides, nach einer Sicherung:

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

*Prüfen:* `$HERMES -p kore-coach mcp test kore` muss „Connected“ und „Tools discovered: 10“ melden.
Scheitert es mit `No module named 'mcp'`, fehlt deinem Hermes das MCP-Extra; `hermes pm install --extra
mcp` holt es nach (Sache von Hermes, `kore-doctor` nennt dieselbe Abhilfe).

*Shell-Befehle in Skills bleiben aus.* In der `config.yaml` des Profils muss `skills.inline_shell`
fehlen (Hermes-Standard) oder genau `false` sein:

```yaml
skills:
  inline_shell: false
```

Mit `true` führt Hermes Zeilen der Form `` !`Befehl` `` aus der `SKILL.md` eines Skills per bash aus,
sobald der Skill angezeigt wird, und der App-Chat hat das Toolset `skills`. Die Bridge **startet
deshalb nicht**, solange die Einstellung an oder nicht eindeutig aus ist (`true`, `yes`, `"false"` in
Anführungszeichen, eine Variable, ein ungewöhnliches Layout; die Meldung nennt Einstellung und
Datei), und `kore-doctor` meldet es. Als eindeutig gelten in dieser Datei nur schlichte
YAML-Schlüssel: Ein Schlüssel in Anführungszeichen (`"skills":`), mit einem Backslash-Escape
(`"inline\x5fshell"` ergibt `inline_shell`) oder in einem Flow-Mapping (`{…}`) gilt als an. Die
Bridge prüft vor jedem Hermes-Lauf erneut: Wird die Datei unsicher, während die Bridge läuft, lehnt
sie jeden Coach-Aufruf mit `503` ab (`code: profile_unsafe`), bis die Datei wieder stimmt, und das
Journal nennt Einstellung und Datei. Hat die Bridge den Start verweigert, starte sie nach der
Korrektur neu.

*Optionaler Zusatzschritt: Dein Hauptassistent liest Kore mit.* Das Coach-Profil ist der Ort, an dem der
Kore-Chat läuft. Soll auch der Assistent, mit dem du täglich sprichst (ein anderes Profil), Fragen aus
deinen Kore-Werten beantworten, kannst du den `kore`-MCP-Server auch in diesem Profil eintragen: mit
derselben `url`, einer eigenen Zeile `MCP_KORE_API_KEY=<dein KORE_MCP_TOKEN>` in der `.env` **dieses**
Profils und nur den Lese-Werkzeugen:

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

(`tools.include` ist der eigene Filter von Hermes; schau in dessen Dokumentation für deine Version.
Ohne `propose_training_plan` kann dieses Profil gar keine Pläne vorschlagen.) Das ist ein Zusatzschritt,
nie der Standard, und er hat dieselben Folgen wie ein bestehendes Profil oben: Jeder Weg, auf dem dieser
Assistent benutzt wird (Messenger, Gruppen, Desktop, geplante Aufgaben), kann dann deine
Gesundheitswerte lesen, **ohne** die Grenzen der Bridge, und sein Gedächtnis und ein externer
Gedächtnis-Anbieter können sie behalten. Dein Modell-Anbieter sieht, was der Assistent abruft. Ein neues
`KORE_MCP_TOKEN` heißt dann, auch die `.env` dieses Profils nachzuziehen. Lass es weg, wenn du es nicht
brauchst; zum Rückgängigmachen löschst du den `kore`-Eintrag wieder.

**6. Die Coach-Bridge einrichten.**

```bash
mkdir -p ~/kore-coach-bridge && chmod 700 ~/kore-coach-bridge
cp ~/kore-server/coach-bridge/bridge.env.example ~/kore-coach-bridge/bridge.env
chmod 600 ~/kore-coach-bridge/bridge.env
```

Bearbeite `bridge.env` (nur absolute Pfade; systemd expandiert weder `$HOME` noch `~`):

| Schlüssel | Wert |
| --- | --- |
| `KORE_BRIDGE_TOKEN` | ein neuer Zufallswert (`openssl rand -hex 24`), mindestens 32 Zeichen; die Bridge lehnt ein kürzeres ab (das Token ist auch der Schlüssel der Chat-Session-Referenzen) |
| `KORE_BRIDGE_BIND_HOST` | dieselbe Adresse wie `KORE_BIND_ADDRESS` (`10.0.0.10`), nie `0.0.0.0`. Mit `127.0.0.1` erreicht dein iPhone die Bridge nicht. |
| `KORE_BRIDGE_PORT` | `8090` |
| `KORE_HERMES_BIN` | absoluter Pfad zu `hermes` |
| `KORE_HERMES_HOME` | das Home-Verzeichnis dieses Nutzers |
| `KORE_HERMES_DATA_DIR` (optional) | nur wenn Hermes seine Daten nicht unter `<Home>/.hermes` ablegt, wie das Hermes-Docker-Image (`/opt/data`). Ein absoluter Pfad ohne `..`, sonst startet die Bridge nicht. Die Bridge gibt ihn als `HERMES_HOME` an Hermes weiter (die einzige Zugabe zur Hermes-Umgebung) und sucht das Profil unter `<Ordner>/profiles/<Name>`; `kore-doctor` sucht dort ebenfalls. Bei der systemd-Unit den Ordner zusätzlich in `ReadWritePaths` eintragen. Siehe [Hermes im Docker-Container](#hermes-im-docker-container-z-b-unraid). |
| `KORE_HERMES_WORK_DIR` (optional) | wo die Hermes-Läufe arbeiten. Standard: `kore-bridge-work` im Datenordner von Hermes (`<Home>/.hermes` oder `KORE_HERMES_DATA_DIR`); die Bridge nutzt darin den Unterordner `chat` und leert ihn vor und nach jedem Lauf. Nur setzen, wenn das Gateway von Hermes diesen Ordner nicht sieht. Nie in einem Git-Checkout; bei der systemd-Unit zusätzlich in `ReadWritePaths` eintragen. |
| `KORE_HERMES_PROFILE` | `kore-coach` |
| `KORE_INSTANCE_ID`, `KORE_PLAN_ORIGIN_PRINCIPAL` | dieselben Werte wie in der `.env` des Servers; später nie ändern |
| `KORE_PLAN_ORIGIN_SECRET` | derselbe Wert wie in der `.env` des Servers |
| `KORE_HERMES_CHILD_PATH` (optional) | der `PATH`, den die Bridge an Hermes gibt. Er **ersetzt** den Standard `/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin`, die Systemordner müssen also drinbleiben. Nötig, wenn dein Modell-Anbieter ein Programm außerhalb davon aufruft, zum Beispiel `/home/<nutzer>/.local/bin:/usr/local/bin:/usr/bin:/bin`. Ohne ihn scheitert der Chat mit `hermes_failed`. `kore-doctor` prüft das. |
| `KORE_COACH_NAME` (optional) | ein Anzeigename für deinen Coach, siehe [Coach-Name](#coach-name) |

`KORE_HERMES_ALLOW_ARGV_PROMPTS` setzt du nie. Dann installierst du den Dienst als
`/etc/systemd/system/kore-coach-bridge.service` (`<USER>`, `<GROUP>` (dessen Gruppe, die aus `KORE_STATE_GID`) und `<HOME>` ersetzen; dieselbe Unit liegt als
`coach-bridge/kore-coach-bridge.service` im Repo):

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

Die Bridge **schreibt** den Kopplungszustand (`<HOME>/kore-server/state`), darum muss dieser Pfad in
`ReadWritePaths` stehen. Fehlt er, macht `ProtectSystem=strict` die Kopplung kaputt: Die Bridge
startet, protokolliert `Device pairing unavailable` und beantwortet jeden Kopplungsversuch mit 401.

`TasksMax`, `MemoryMax` und `LimitNOFILE` begrenzen die ganze Unit: die Bridge (höchstens 64
Verbindungen) und die Hermes-Läufe, die sie startet (höchstens 2 gleichzeitig, jeder mit Python und
seinen MCP-Clients). Zeigt das Journal `fork failed`, `Too many open files` oder einen
OOM-Kill, erhöhe den Wert. Danach:

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now kore-coach-bridge
```

(Liegt dein Zustandsordner nicht unter `<HOME>/kore-server/state`, setze `KORE_STATE_DIR` in
`bridge.env` und `.env` auf denselben absoluten Pfad und nimm diesen Pfad in `ReadWritePaths`; systemd
expandiert dort keine Variablen.) Ist eine Firewall wie
`ufw` aktiv, gib nur Port 8090 für dein lokales Subnetz frei. Docker umgeht `ufw` für seine eigenen
Ports, und der MCP-Port ist ohnehin lokal.

**7. Alles prüfen.** Installiere vorher `qrencode`, damit `kore-doctor pair` einen QR-Code zeigen kann
(`sudo apt install qrencode`; ohne bekommst du nur den Link).

```bash
python3 ~/kore-server/scripts/kore-doctor
```

Der Exit-Code muss 0 sein. Siehe [kore-doctor](#installation-prüfen-kore-doctor). Danach
[koppelst du dein iPhone](#kopplung-mit-dem-iphone).

## Hermes im Docker-Container (z. B. Unraid)

Läuft dein Hermes-Agent im Hermes-Docker-Image (alle Daten unter `/opt/data`), zum Beispiel auf Unraid,
folgt die Einrichtung [Weg B](#weg-b-von-hand-einrichten) mit einigen Unterschieden. Einen fertigen
Container für die Coach-Bridge gibt es nicht: Du startest sie als Prozess im Hermes-Container. Unten
steht `<hermes>` für den Namen deines Hermes-Containers und `<container-adresse>` für dessen feste
Adresse.

| | Hermes auf dem Host (Weg B) | Hermes im Container |
| --- | --- | --- |
| Ingest und MCP | Docker Compose auf dem Host | dasselbe, auf dem Docker-Host |
| MCP-Adresse im Profil | `http://127.0.0.1:8000/mcp/` | `http://mcp:8000/mcp/`, im Compose-Netz |
| Hermes-Daten | `<Home>/.hermes` | `/opt/data`, der Bridge über `KORE_HERMES_DATA_DIR` mitgeteilt |
| Coach-Bridge | systemd-Dienst | ein Prozess im Hermes-Container, auf einer festen Container-Adresse |
| Bridge neu starten | `sudo systemctl restart kore-coach-bridge` | den Prozess neu starten (Schritt 5) |

**1. Der Code und zwei Ordner für den Hermes-Container.** Hol den Code auf dem Docker-Host wie in
Schritt 1 von Weg B. Binde dann das Checkout schreibgeschützt in den Hermes-Container ein (zum Beispiel
als `/opt/kore-server`) und einen Ordner, den der Container-Nutzer beschreiben darf (zum Beispiel als
`/opt/kore-bridge`). Der zweite nimmt `bridge.env` und den Kopplungszustand `state/` auf. Das Checkout
enthält die `.env` deines Servers; soll der Hermes-Container sie nicht sehen, binde eine Kopie des
Checkouts ohne `.env` ein (Bridge und Doctor brauchen `coach-bridge/`, `scripts/`, `hermes/` und
`contract/`). Leg den beschreibbaren Ordner auf dem Host an, mit dem Nutzer als Besitzer, unter dem
Hermes im Container läuft (`docker exec <hermes> id`; das Image nutzt `hermes`), Rechte 0750, und lege
dann den Kopplungszustand **vor** dem ersten `docker compose up` an, als dieser Nutzer:

```bash
docker exec --user hermes <hermes> python3 /opt/kore-server/scripts/kore-doctor init-state \
  --state-dir /opt/kore-bridge/state
```

Das gibt die Zeile `KORE_STATE_GID=` für die Server-`.env` aus.

**2. Ingest und MCP.** Führ die Schritte 3 und 4 von Weg B auf dem Docker-Host aus. In der Server-`.env`,
neben `KORE_STATE_GID`: `KORE_STATE_DIR` auf den Pfad des Kopplungszustands **auf dem Host** (Compose
bindet ihn schreibgeschützt in den Ingest ein), optional `KORE_INGEST_PORT` und `KORE_MCP_PORT`, wenn
ein Host-Port belegt ist (siehe Schritt 3), und

```
KORE_MCP_URL=http://mcp:8000/mcp/
```

Compose ignoriert `KORE_MCP_URL`; nur `kore-doctor` liest sie. Der MCP liegt dann nicht auf dem Loopback
des Rechners, auf dem der Doctor läuft, also überspringt der Doctor seine Prüfung auf `127.0.0.1` mit
einer `[HINWEIS]`-Zeile, statt zu scheitern, und überlässt den Weg zum MCP `hermes mcp test kore`. Der
MCP bleibt nur auf `127.0.0.1` des Hosts veröffentlicht.

**3. Den Hermes-Container ins Compose-Netz holen.** Im Hermes-Container ist `127.0.0.1` der Container
selbst, also erreicht er den MCP über dessen Compose-Dienstnamen:

```bash
docker network ls                                  # das Kore-Projektnetz, z. B. kore-server_default
docker network connect kore-server_default <hermes>
```

Jeder Container in diesem Netz erreicht den MCP-Port (das Token bleibt Pflicht); halte das Netz also bei
den beiden Kore-Containern und Hermes. Docker behält die Verbindung nicht, wenn der Hermes-Container neu
erzeugt wird: Führ den Befehl erneut aus, oder trag das Netz in der eigenen Definition des
Hermes-Containers ein.

**4. Das Coach-Profil.** Führ Schritt 5 von Weg B im Hermes-Container aus (`docker exec -it --user
hermes <hermes> hermes profile create kore-coach` und so weiter; das Profil liegt in
`/opt/data/profiles/kore-coach`), mit zwei Unterschieden: Die MCP-`url` ist `http://mcp:8000/mcp/`, und
`MCP_KORE_API_KEY` steht in `/opt/data/profiles/kore-coach/.env`. Es gilt dieselbe Wahl: ein neues
Coach-Profil (empfohlen) oder ein bestehendes mit den dort genannten Folgen. Prüfen mit `docker exec
--user hermes <hermes> hermes -p kore-coach mcp test kore`: „Connected“, „Tools discovered: 10“.

**5. Die Bridge.**

- *Adresse.* Die Bridge lauscht auf genau einer konkreten Adresse, nie auf `0.0.0.0`, und `127.0.0.1`
  erreicht dein iPhone nicht. Gib dem Hermes-Container eine feste Adresse: entweder eine eigene Adresse
  in deinem Heimnetz (auf Unraid ein eigenes Netz wie `br0` mit fester IP), die dein iPhone dann direkt
  erreicht, oder eine feste Adresse in einem selbst angelegten Docker-Netz mit auf dem Host
  veröffentlichtem Port (`-p 10.0.0.10:8090:8090`); dann gibst du `kore-doctor pair` zusätzlich
  `--bridge-url http://10.0.0.10:8090` mit. Akzeptiert werden nur Loopback-, RFC-1918- und
  `100.64.0.0/10`-Adressen. Ändert sich die Adresse nach einem Neustart, scheitert die Bridge mit „Cannot
  assign requested address“.
- *`bridge.env`* in `/opt/kore-bridge` (`chmod 600`), wie in Schritt 6 von Weg B, mit diesen Werten:
  `KORE_BRIDGE_BIND_HOST=<container-adresse>`; `KORE_HERMES_BIN=` der Pfad von `hermes` im Container
  (`docker exec --user hermes <hermes> sh -c 'command -v hermes'`); `KORE_HERMES_HOME=` das Home des
  Hermes-Nutzers im Container (im Image: `/opt/data`); `KORE_HERMES_DATA_DIR=/opt/data`; und
  `KORE_STATE_DIR=/opt/kore-bridge/state`, der Pfad des Kopplungszustands **im Container**. Schreib die
  Werte ohne Anführungszeichen: Dockers `--env-file` (unten) gibt sie wörtlich weiter.
- *Starten und neu starten.* Ohne systemd ist die Bridge ein Prozess, den du im Container als Hermes-Nutzer
  (nicht als root) startest, mit der Kopie von `bridge.env` auf dem Host:

  ```bash
  docker exec --user hermes <hermes> pkill -f coach-bridge/bridge.py     # nur zum Neustart: beendet den alten
  docker exec -d --user hermes --env-file /host/pfad/zu/kore-bridge/bridge.env <hermes> \
    sh -c 'exec python3 /opt/kore-server/coach-bridge/bridge.py >> /opt/data/logs/kore-bridge.log 2>&1'
  ```

  Die erste Zeile scheitert, wenn das Image kein `pkill` hat; ein Neustart des Hermes-Containers beendet
  ebenfalls jeden Prozess darin. Nach einem Neustart oder einer Neuerzeugung des Hermes-Containers ist
  der Prozess weg: Führ den Startbefehl erneut aus, zum Beispiel aus einem Skript, das dein Host nach dem
  Start des Containers ausführt. Die Bridge kann auch in einem eigenen Container laufen, der dieselbe
  Hermes-Installation trägt und dasselbe `/opt/data` einbindet; ein Neustart ist dann `docker restart`
  dieses Containers, und er darf kein zweites Hermes-Gateway starten.
- *Prüfen.* `docker exec --user hermes <hermes> python3 /opt/kore-server/scripts/kore-doctor
  --bridge-env /opt/kore-bridge/bridge.env --bridge-only` prüft Bridge und Hermes. Ingest und MCP prüfst
  du vom Host aus mit den `curl`-Zeilen aus Schritt 4 von Weg B (mit deinen Ports). Kann der
  Hermes-Container die Server-`.env` sehen, lass `--bridge-only` weg: Der Doctor vergleicht dann auch die
  gemeinsamen Geheimnisse und prüft den Ingest.

Danach [koppelst du dein iPhone](#kopplung-mit-dem-iphone) wie gewohnt. Zwei Dinge sind gegenüber Weg B
sicherheitlich anders: Die Bridge, Hermes und alles andere in diesem Container laufen unter demselben
Nutzer, also kann alles im Container mit Datei- oder Terminal-Werkzeugen `bridge.env` lesen (siehe
[Netz](#netz), „Die Vertrauensgrenze ist der Linux-Nutzer“); und der MCP ist von jedem Container im
Compose-Netz erreichbar.

## Kopplung mit dem iPhone

Statt URLs und Tokens in die App zu tippen, **koppelt** sich Kore mit einem Einmalcode an deinen
Server. Auf dem Server, als Nutzer der Bridge:

```bash
python3 ~/kore-server/scripts/kore-doctor pair
```

Das gibt einen Link der Form `kore://pair?v=1&bridge=…&ingest=…&code=…&sid=…` aus und, wenn
`qrencode` installiert ist (`apt install qrencode`), einen QR-Code im Terminal. Dann entweder

- **den Link auf dem iPhone antippen** (schick ihn dir über einen Kanal, dem du vertraust), oder
- **den QR-Code mit Kore scannen** (Einstellungen › Experimentelle Funktionen).

Kore fragt „Mit diesem Server koppeln?“ und zeigt Adresse und Server-ID. Nach deiner Bestätigung
tauscht es den Code gegen ein **Gerätetoken**, legt es im iOS-Schlüsselbund ab und zeigt „Gekoppelt
seit …“. Der genaue Wortlaut der Knöpfe kann sich zwischen App-Versionen leicht unterscheiden.

Wie es funktioniert und was es zusichert:

- Der Code hat 20 Zeichen (100 Bit), gilt **10 Minuten** und wirkt **einmal**: Eine gelungene
  Kopplung verbraucht ihn. Eine Anfrage, die gar keinen wohlgeformten Code trägt (falsche Länge oder
  Zeichen, kein Text, kein JSON), rührt den offenen Code nicht an. Nach **1000 Fehlversuchen** mit einem
  wohlgeformten Code ist dieser Code tot, und du erzeugst einen neuen. Die Grenze ist absichtlich hoch:
  100 Bit lassen sich in 10 Minuten ohnehin nicht erraten, und eine niedrige Grenze ließe jeden in
  deinem Netz deinen offenen Code mit ein paar falschen Anfragen töten. Ein neuer Code ersetzt außerdem
  den offenen. Von einer Adresse nimmt der Server höchstens 5 Fehlversuche pro Minute an und antwortet
  danach mit 429; Prüfen und Zählen sind ein Schritt, parallele Anfragen kommen also nicht am Limit
  vorbei.
- Der Link enthält nie ein langlebiges Token. Ein Gerätetoken sind 256 Bit Zufall; der Server
  speichert nur dessen SHA-256-Hash, im Zustandsordner, und protokolliert weder Codes noch Tokens.
- Das Gerätetoken gilt für die Bridge und die Ingest-API, sonst nichts. Der MCP-Server akzeptiert nur
  sein eigenes Token, das Geräte nie zu sehen bekommen.
- Ein abgefangener Link nützt außerhalb deines Heimnetzes oder VPNs nichts (der Server ist dort nicht
  erreichbar), gilt nur einmal und verfällt nach 10 Minuten.
- **Koppel in einem Netz, dem du vertraust.** Die Bridge spricht Klartext-HTTP, Code und neues
  Gerätetoken laufen in diesem Moment also unverschlüsselt durchs Netz. Koppel zuhause. Unterwegs nur
  über das VPN deines Routers oder die HTTPS-Namen von Tailscale (siehe [VPN](#unterwegs-per-vpn)),
  nie per Klartext-HTTP in einem Netz, das du nicht kontrollierst.

Nützliche Optionen: `--name "Mein iPhone"` benennt das Gerät, das als Nächstes koppelt;
`--bridge-url` und `--ingest-url` setzen die Adressen, falls sie sich nicht aus `bridge.env` und
`.env` ableiten lassen. Akzeptiert werden private IPv4-Adressen (10/8, 172.16/12, 192.168/16; `http` oder
`https`), `https://<name>.ts.net` und `https` an eine Tailscale-Adresse (100.64/10) (siehe
[VPN](#unterwegs-per-vpn)); Klartext-`http` an Tailscale-Adressen wird abgelehnt, weil iOS es blockiert.
Nie Loopback, andere Hostnamen oder öffentliche Adressen.

**Geräte verwalten:**

```bash
python3 ~/kore-server/scripts/kore-doctor devices         # ID, Name, gekoppelt, zuletzt gesehen (nie Tokens)
python3 ~/kore-server/scripts/kore-doctor revoke 1a2b3c4d  # ein Gerät sofort aussperren
```

Ein widerrufenes Gerät wird bei seiner nächsten Anfrage abgewiesen, von Bridge und Ingest-API
gleichermaßen. „Entkoppeln“ in der App entfernt das Token auf dem Handy und sagt dem Server, das
Gerät zu vergessen. Ein Handy verloren oder ersetzt? `revoke`. Das vollständige Protokoll steht in
[`contract/pairing-v1.md`](contract/pairing-v1.md) (englisch).

**Experten-Weg mit statischen Tokens.** Die statischen Tokens (`KORE_INGEST_TOKEN`,
`KORE_BRIDGE_TOKEN`) bleiben gültig. Trag in Kore unter **Einstellungen › Experimentelle Funktionen**
vier Werte von Hand ein: Server-URL (`http://10.0.0.10:8081`), Ingest-Token, Coach-Bridge-URL
(`http://10.0.0.10:8090`) und Coach-Token. Das ist der Weg, wenn du ihn vorziehst, wenn dein Kore
älter als 0.13 ist oder die Kopplung nicht verfügbar ist. Übertrag die Tokens nur über einen Kanal,
dem du vertraust, nie per Messenger oder Notiz-App; Kore entfernt beim Einfügen Leerzeichen,
Zeilenumbrüche und ein vorangestelltes „Bearer“.

Dann in Kore: den Apple-Health-Zugriff erlauben, **Coach aktiv** und **Health-Snapshot
synchronisieren** einschalten, optional **Health-Kontext mit Coach teilen** (es fragt noch einmal
nach), die iOS-Frage nach „Lokales Netzwerk“ erlauben, **Jetzt synchronisieren** und **Coach-Bridge
testen** tippen. Die erste Nachricht schickst du über den Coach-Knopf in der Kopfzeile. Eine gute
erste Frage ist eine, deren Antwort du selbst nachprüfen kannst: *„Wie war meine Erholung diese
Woche, und was fällt dir an meinem Schlaf auf?“*

## Coach-Name

Dein Coach kann einen Namen haben, den Kore im Chat anzeigt. Trag ihn in `bridge.env` ein und starte
die Bridge neu:

```
KORE_COACH_NAME=Coach Lena
```

Der Name wird getrimmt und muss 1 bis 24 Zeichen lang sein: Buchstaben (jeder Sprache), Ziffern,
Leerzeichen, Bindestrich, Apostroph und Punkt, sonst nichts. Ein ungültiger Wert wird **ignoriert**,
mit einer Warnung im Journal beim Start; die Bridge startet trotzdem, und die App benutzt ihren
Standardnamen. `GET /health` und `GET /capabilities` **der Bridge** enthalten den Namen als
`"coach_name": "Coach Lena"` nur, wenn er gültig ist; ohne gültigen Namen fehlt das Feld. Ein eigenes
Capability-Flag gibt es dafür nicht: Das Vorhandensein des Feldes ist das Signal. Der Name ändert nur,
was die App anzeigt, nicht die Persönlichkeit des Coaches (die steht in der `SOUL.md` des Profils).

## Unterwegs per VPN

Zuhause erreicht Kore den Server direkt über dein Heimnetz. Unterwegs nimmst du ein **VPN in dein
Heimnetz**. Die Regel: **nie einen Port weiterleiten, nie an `0.0.0.0` binden, nie einen öffentlichen
Tunneldienst nutzen** (Reverse-Tunnel, Tailscale Funnel und Ähnliches). Alle drei Dienste sprechen
Klartext-HTTP im eigenen Netz; im VPN-Tunnel verschlüsselt der Tunnel diesen Verkehr, zuhause nicht.
Die Ausnahme ist Tailscale, das HTTPS braucht (Variante 2).

Jeder Dienst lauscht auf **einer** Adresse, such also die Adresse aus, die dein iPhone immer benutzen
soll.

### Variante 1: WireGuard im Router (zum Beispiel FRITZ!Box)

Viele Router können als WireGuard-Server arbeiten. Leg in den VPN-Einstellungen des Routers eine
Verbindung für dein iPhone an, importier die Konfiguration in die WireGuard-App auf dem iPhone und
sorg dafür, dass der Tunnel dein Heimnetz-Subnetz routet (die `AllowedIPs` der iPhone-Konfiguration
enthalten es, etwa `10.0.0.0/24`). Das Handy erreicht den Server dann unter **derselben Adresse wie
zuhause** (`10.0.0.10`). Auf dem Server ändert sich nichts: `KORE_BIND_ADDRESS` und
`KORE_BRIDGE_BIND_HOST` bleiben auf der LAN-Adresse (mit DHCP-Reservierung), und du koppelst wie
gewohnt. Der VPN-Port des Routers ist der Endpunkt des VPNs selbst, kein weitergeleiteter Kore-Port.
Weil das Handy eine Heimnetz-Adresse bekommt, funktioniert Klartext-`http` genau wie zuhause.

### Variante 2: Tailscale mit `tailscale serve` (HTTPS)

iOS verweigert unverschlüsseltes `http` an Tailscale-Adressen (`100.64.0.0/10`) und an `*.ts.net`-Namen
(App Transport Security). Die Tailscale-Adresse selbst lässt sich deshalb nicht so benutzen wie die
Heimnetz-Adresse. Was funktioniert: `tailscale serve` veröffentlicht die Dienste innerhalb deines Tailnets
mit Zertifikat. Kore spricht dann `https://<name>.ts.net`. `tailscale serve` leitet an **die Adresse
weiter, auf der der Dienst lauscht**; deshalb gibt es zwei stimmige Aufbauten:

- **Empfohlen: die Dienste auf der LAN-Adresse lassen** (`KORE_BIND_ADDRESS` und
  `KORE_BRIDGE_BIND_HOST` wie in der Einrichtung, zum Beispiel `10.0.0.10`). Heimnetz und Router-VPN
  funktionieren weiter mit Klartext-`http`, und `tailscale serve` leitet an `http://10.0.0.10:8081`
  und `http://10.0.0.10:8090` weiter.
- **Nur Tailscale:** beide Bind-Adressen auf `127.0.0.1` setzen und `tailscale serve` an
  `http://127.0.0.1:8081` und `http://127.0.0.1:8090` weiterleiten lassen. Dann lauscht nichts auf
  einer Netzwerkadresse, aber das iPhone erreicht den Server nicht mehr über die Heimnetz-Adresse,
  nur noch über die `ts.net`-Namen.

1. **Tailscale auf dem Server installieren** nach der Anleitung von Tailscale für deine
   Distribution und anmelden (`sudo tailscale up`).
2. **Die Tailscale-App auf dem iPhone installieren** und mit demselben Konto anmelden.
3. **Einen unauffälligen Rechnernamen wählen** (in der Admin-Konsole oder mit
   `sudo tailscale set --hostname=<name>`). HTTPS-Zertifikate tragen Rechnername und Name deines
   Tailnets in öffentliche Zertifikats-Logs (Certificate Transparency) ein, die jeder durchsuchen
   kann. Also nicht `<dein-name>-gesundheitsserver`, sondern etwas Neutrales. Schalte die
   HTTPS-Zertifikate in der Admin-Konsole ein.
4. **Die Bind-Adresse festlegen.** Empfohlen: nichts ändern, die Dienste lauschen weiter auf der
   LAN-Adresse. Nur Tailscale: in der `.env` des Servers `KORE_BIND_ADDRESS=127.0.0.1`, in
   `bridge.env` `KORE_BRIDGE_BIND_HOST=127.0.0.1`, dann `docker compose up -d` und
   `sudo systemctl restart kore-coach-bridge`.
5. **Im Tailnet veröffentlichen.** Das Ziel ist die Adresse aus Schritt 4 (hier `10.0.0.10`; bei
   „nur Tailscale“ `127.0.0.1`):

   ```bash
   sudo tailscale serve --bg --https=443  http://10.0.0.10:8081    # Ingest
   sudo tailscale serve --bg --https=8443 http://10.0.0.10:8090    # Bridge
   ```

   Hast du den Ingest-Port verlegt (`KORE_INGEST_PORT`), nimm ihn im ersten Befehl. Die Flags deiner
   Version zeigt `tailscale serve --help`. Nie `tailscale funnel` verwenden; das
   veröffentlicht ins offene Internet.
6. **Mit den HTTPS-Namen koppeln.** Gib sie ausdrücklich an: `kore-doctor pair` würde sonst aus den
   Bind-Adressen `http`-Links bauen, die iOS für Tailscale nicht akzeptiert:

   ```bash
   python3 ~/kore-server/scripts/kore-doctor pair \
     --bridge-url https://<rechner>.<tailnet>.ts.net:8443 \
     --ingest-url https://<rechner>.<tailnet>.ts.net
   ```

   Schalt Tailscale auf dem Handy ein und öffne dann den Link. `kore-doctor pair` lehnt Klartext-`http`
   an `100.x`-Adressen und `*.ts.net`-Namen ab und sagt dir das.
7. **Halte dein Tailnet persönlich** und seine Zugriffsregeln eng: Alles, was diese Namen erreicht,
   kann deine Tokens ausprobieren.

Mit eingeschalteter Tailscale-App gelten dieselben Namen zuhause und unterwegs.

## Sicherheit

### Prinzipien

Diese Punkte sind keine Empfehlungen, sondern die Annahmen, unter denen der Code geschrieben wurde.
Wer sie verletzt, betreibt etwas anderes als das hier beschriebene System.

1. **Nur der verdichtete Snapshot, nie Health-Rohwerte.** Die App schickt Tageswerte: Erholung,
   HRV als ein Wert je Tag (SDNN in ms, auch als Verlauf über die letzten Tage), Ruhepuls,
   Schlafstunden, Workout-Zusammenfassungen und seit 0.14 Tagessummen der Schritte sowie der letzte
   Wert des Tages für Gewicht und Körperfett. Ernährung (Tagessummen von Energie, Eiweiß,
   Kohlenhydraten und Fett) wird nur geschickt, wenn du in Kore ihre eigene Freigabe einschaltest und
   Kore in Health das Lesen erlaubst; Ausschalten lässt sie beim nächsten Sync auf dem Server leeren.
   Alles geht nur an deinen eigenen Server und deinen eigenen Coach. Keine Herzschlagintervalle, keine
   Messreihen innerhalb eines Tages, keine HealthKit-Exporte. Die MCP-Werkzeuge liefern genau diese Werte weiter und
   können nichts anderes herausgeben, weil nichts anderes da ist.
2. **Schreiben nur als Plan-Vorschlag mit Bestätigung in der App.** Das einzige schreibende Werkzeug
   ist `propose_training_plan`. Es legt den Plan mit `status=pending` ab. Die App holt ihn, zeigt ihn
   dir, und erst dein Tippen auf „Übernehmen“ macht ihn wirksam. Ein Agent kann in Kore nichts still
   aktivieren. Zusätzlich braucht jeder Schreibvorgang ein kurzlebiges, HMAC-signiertes Einmal-Ticket
   der Coach-Bridge (`KORE_PLAN_ORIGIN_SECRET`); der MCP-Token allein genügt nicht.
3. **Kein Port-Forward.** Alle Ports gehören ins Heimnetz, auf eine VPN-Adresse oder auf Loopback (Loopback ist die Wahl, wenn du nur Tailscale nutzt und `tailscale serve` dorthin weiterleitet).
   Bridge, Ingest-API und MCP-Server weigern sich, auf `0.0.0.0` zu lauschen, und akzeptieren nur
   eine konkrete IPv4-Loopback-, RFC-1918- oder VPN-Adresse (100.64.0.0/10) (in Docker gilt die
   Prüfung der Adresse, auf der Compose den Port veröffentlicht). Der MCP-Port ist nur auf `127.0.0.1`
   veröffentlicht und aus deinem Netz gar nicht erreichbar.
4. **Getrennte Tokens, lang genug, und du rotierst sie.** Ingest, Bridge und MCP haben je ein eigenes
   statisches Token mit mindestens 32 Zeichen, damit ein kompromittierter Agent nicht schreiben darf,
   was die App darf; die Dienste starten mit einem kürzeren nicht. Unter Docker bekommt außerdem jeder
   Container nur seine eigenen Schlüssel (siehe [Dienste und Container](#dienste-und-container)). Siehe
   [Geheimnisse tauschen](#geheimnisse-tauschen).
5. **Der `Origin`-Header wird geprüft.** Sonst könnte eine Webseite, die du im Browser offen hast,
   deinen lokalen Server ansprechen (DNS-Rebinding). Eine eigene ASGI-Schicht
   (`server/origin_guard.py`) sitzt vor Ingest und MCP, und die Kopplungs-Routen der Bridge weisen
   jede Anfrage mit `Origin` ab. Anfragen ohne `Origin` (App, Agent, `curl`) kommen durch; ein
   vorhandener `Origin` muss in `KORE_MCP_ALLOWED_ORIGINS` stehen (Standard leer, also „gar kein
   Browser“), sonst 403. Das ersetzt den Token nicht; es ist das zweite Schloss an derselben Tür.
6. **Der Prompt geht über stdin, nicht über die Kommandozeile.** Alles in `argv` ist solange der
   Prozess läuft in `/proc/<pid>/cmdline` für jeden lokalen Nutzer lesbar. Beim Gesundheits-Chat
   wäre das der Gesundheitskontext. Die Bridge ruft `hermes chat --query-file -` und schreibt den
   Prompt nach stdin. Mit `KORE_HERMES_ALLOW_ARGV_PROMPTS=1` lehnt die Bridge jeden Coach-Aufruf ab
   (503) und warnt beim Start. Jeder Prompt beginnt außerdem mit einer festen ersten Zeile,
   `[KORE-APP-NACHRICHT]`: Im Einmal-Modus liest Hermes einen Dateipfad ganz am Anfang einer Nachricht
   (`/…`, `~/…`, `./…`, `file://…`, `C:/…`) als Anhang, und eine Bilddatei ginge so am Vision-Modell
   vorbei an der Werkzeugliste. Mit der festen Zeile vorn steht der Text einer Nachricht nie am Anfang.
7. **Tool Poisoning.** Ein MCP-Client entscheidet anhand von Werkzeug-*Beschreibungen*, was er tut.
   Wer einen bösartigen MCP-Server in dasselbe Profil einträgt, das `kore` sieht, kann in dessen
   Beschreibungen Anweisungen verstecken. In dem Profil, das `kore` sieht, nur Werkzeug-Server, deren
   Quelle du kennst. Das kann dieser Server nicht für dich lösen; er kann nur klein halten, was zu
   holen ist (Punkt 1).
8. **Die Kopplung ist einmalig, kurz und widerrufbar.** Codes sind zufällig, gelten 10 Minuten, sind
   einmal nutzbar, nach 1000 Fehlversuchen tot und je Adresse gebremst. Gerätetokens sind zufällig,
   werden nur als Hash gespeichert und lassen sich jederzeit widerrufen. Weder Codes noch Tokens
   werden protokolliert.
9. **Die Anmeldung kommt vor dem Body.** Die Ingest-API prüft das Bearer-Token in einer Schicht vor dem
   Web-Framework, bevor sie ein einziges Byte der Anfrage liest: Ohne gültiges Token kommt sofort 401,
   auch bei einem 16-MiB-Upload. Offen ist nur der exakte Pfad `/health` (er nimmt höchstens 4 KiB
   an); jeder andere Pfad, auch ein unbekannter, braucht zuerst das Token. Es zählt genau ein
   `Authorization`-Header; eine Anfrage mit zwei wird abgewiesen, auch an der Coach-Bridge und am
   MCP-Server. `/docs`, `/redoc` und `/openapi.json` gibt es nicht.
10. **Keine Websuche dort, wo der Health-Kontext ist, außer auf deinen ausdrücklichen Wunsch.** Ein
    Health-Chat (und die Tages-Anpassung, die ebenfalls Health-Kontext trägt) bekommt die Werkzeuge
    `skills,memory,kore`, nie `web`; ein in so einen Chat eingeschleuster Text hat also kein Werkzeug,
    um den Health-Kontext an eine Adresse seiner Wahl zu schicken. Nur wenn die Person die Websuche für
    den Health-Chat in der App einschaltet (Anfragefeld `health_web_allowed: true`, standardmäßig aus),
    bekommt dieser Chat `web,skills,memory,kore`; die Tages-Anpassung nie. Ein allgemeiner Chat hat die
    Websuche, bekommt aber weder Health-Kontext noch das `kore`-MCP. Zwei Wege bleiben, siehe
    [Was bleibt](#was-bleibt-restrisiken): der Wunsch selbst und ein indirekter Weg in zwei Schritten
    über das Gedächtnis des Profils. Das Protokoll steht in
    [`contract/health-web-opt-in-v1.md`](contract/health-web-opt-in-v1.md) (englisch).

### Netz

- **Kein Port-Forward. Niemals.** Was du freigäbst, ist ein Gesundheitsverlauf mit einem einzigen
  Bearer-Token davor. Von unterwegs geht es über ein [VPN](#unterwegs-per-vpn).
- **Klartext-HTTP.** Alle drei Dienste sprechen HTTP ohne TLS. Wer in deinem Netz mitlesen kann, sieht
  Tokens und Inhalte. Nutz es nur in einem Netz, dem du vertraust; Gäste und fremde Geräte gehören in
  ein eigenes WLAN. Ein VPN-Tunnel verschlüsselt den Weg durchs Internet. Das gilt auch für die
  [Kopplung](#kopplung-mit-dem-iphone): Koppel zuhause, unterwegs nur über das Router-VPN oder die
  HTTPS-Namen von Tailscale.
- **Docker vor Version 28.** Bis Docker 27 ließ sich ein auf `127.0.0.1` veröffentlichter Port von
  anderen Rechnern im selben Layer-2-Segment erreichen, etwa im selben WLAN (moby-Issue #45610). Beim
  MCP-Port höbe das „nur dieser Rechner“ auf. Nimm Docker 28 oder neuer, oder ergänze in der
  Host-Firewall (nftables) eine Regel, die Verkehr zu einem auf `127.0.0.1` veröffentlichten Port
  verwirft, wenn er nicht über die Loopback-Schnittstelle ankommt. Die `kore-doctor`-Prüfung „MCP über
  die Netzadresse nicht erreichbar“ schließt diesen Fall nicht aus.
- **`KORE_MCP_ALLOWED_ORIGINS` bleibt leer**, außer du hängst wirklich eine eigene Weboberfläche davor.
- **Geheimnisse liegen im Klartext.** `.env`, `bridge.env` und die `.env` des Profils: `chmod 600`,
  dem Dienstnutzer gehörend, nie in ein Backup, das anderswo landet, nie in ein Git-Repo, einen
  Screenshot oder ein Chatfenster. Die `.gitignore` hält `*.env`, Datenbanken, `*.bak*` und
  Schlüsseldateien aus Git, die `.dockerignore` aus dem Image.
- **Die Vertrauensgrenze ist der Linux-Nutzer.** Dateirechte trennen zwei Programme nicht, die unter
  demselben Nutzer laufen. Alles von diesem Nutzer mit Terminal- oder Datei-Werkzeugen kann
  `bridge.env`, die `.env` des Profils, die `.env` des Servers und den Kopplungszustand lesen: ein
  anderes Hermes-Profil, ein Messenger-Gateway, ein Cronjob. Der App-Chat hat solche Werkzeuge nicht,
  andere Oberflächen desselben Linux-Nutzers vielleicht. Am besten: Bridge und Coach-Profil unter einem
  eigenen Linux-Nutzer betreiben, ohne weiteren Agenten. Mindestens ein eigenes Hermes-Profil für Kore
  (das trennt Gedächtnis und Konfiguration, nicht Dateien) und wissen, was deine anderen Profile
  dieses Nutzers dürfen.
- **Lieferkette.** Abhängigkeiten werden nur aus Lockfiles mit Hashes installiert
  (`--require-hashes`), das Basis-Image ist per Digest festgelegt, GitHub-Actions stehen auf
  Commit-SHAs, und ein gitleaks-Job sucht im gesamten Verlauf nach Geheimnissen.

### Dienste und Container

- **Docker Compose.** Es gibt kein `env_file`: Jeder Container bekommt nur die Schlüssel, die er
  braucht, wie in der `docker-compose.yml` aufgelistet. Der Ingest hält weder das MCP-Token noch das
  Origin-Geheimnis (er liest nur den Principal), der MCP-Server hält kein Ingest-Token und keinen
  Zustandsordner. Beide Container laufen mit `read_only: true`, `/tmp` als kleinem `noexec`-tmpfs,
  allen Capabilities abgelegt, `no-new-privileges`, `pids_limit: 128` und einer Speichergrenze (768m
  Ingest, 384m MCP; ein 16-MiB-Upload braucht beim Parsen grob 400 bis 500 MiB). Schreibbar ist nur
  das Volume `kore-db:/data`. Der Ingest-Container hat einen Healthcheck.
- **Wo sie lauschen.** Das Docker-Image lauscht im Container auf allen Schnittstellen (nur so
  erreicht die Port-Weiterleitung es); die Angriffsfläche ist die Host-Adresse, auf der Compose den
  Port veröffentlicht. Compose reicht diese Adresse als `KORE_PUBLISHED_BIND` in den Container, der
  Prozess prüft sie nach derselben Regel wie die Bridge und startet sonst nicht. `KORE_PUBLISHED_BIND`
  gehört in die `docker-compose.yml`, nicht in die `.env`. Ohne Docker prüfen die Dienste ihre eigene
  Lausch-Adresse: Die Ingest-API liest `KORE_INGEST_HOST`, sonst `KORE_BIND_ADDRESS`, der MCP-Server
  `KORE_MCP_HOST`; ein nicht gesetzter oder leerer Wert bedeutet `127.0.0.1`, Wildcards werden
  abgelehnt.
- **systemd.** Die Units in [`deploy/`](deploy/) laufen in einer Sandbox (`ProtectSystem=strict`,
  `PrivateTmp`, Schutz von Kernel und Control Groups, eingeschränkte Adressfamilien und Namespaces,
  `NoNewPrivileges`, leere Capability-Menge); die Bridge-Unit oben hat eine leichtere Sandbox derselben
  Art. Alle drei tragen `MemoryMax`, `TasksMax` und `LimitNOFILE`. Die Ingest-API startet über
  `python ingest_api.py`, das die Adresse prüft; eine selbst geschriebene Unit, die `uvicorn` direkt
  startet, überspringt diese Prüfung. Unter systemd lesen beide
  Server-Units dieselbe `.env` unter demselben Nutzer; die Schlüsseltrennung von Compose gibt es dort
  also nicht, und gegen einen Prozess desselben Nutzers würde sie auch nichts nützen.

### Geheimnisse tauschen

| Geheimnis | Wo | Wer es sonst noch braucht |
| --- | --- | --- |
| `KORE_INGEST_TOKEN` | `.env` des Servers | Kore, nur auf dem Experten-Weg |
| `KORE_MCP_TOKEN` | `.env` des Servers | `.env` des Profils als `MCP_KORE_API_KEY` (und die jedes anderen Profils, dem du Lesezugriff gegeben hast) |
| `KORE_BRIDGE_TOKEN` | `bridge.env` | Kore, nur auf dem Experten-Weg |
| `KORE_PLAN_ORIGIN_SECRET` | `.env` des Servers **und** `bridge.env` (gleicher Wert) | niemand sonst |
| Gerätetokens | serverseitig nur als Hash | `kore-doctor revoke <id>` hebt eines auf |

Neu erzeugen (`openssl rand -hex 24`, für das Origin-Geheimnis `-hex 32`), eintragen, neu starten
(`docker compose up -d --force-recreate`, `sudo systemctl restart kore-coach-bridge`), die
Gegenstelle nachziehen, danach `kore-doctor`. Tu das, wenn du den Agenten wechselst, einen Rechner
abgibst, ein Token irgendwo gelandet ist, wo es nicht hingehört, oder du einen Verdacht hast. Ein
neues `KORE_BRIDGE_TOKEN` macht zusätzlich alle Chat-Referenzen ungültig: Jeder Chat beginnt mit
„Kontext neu begonnen“, die alten Hermes-Sessions verfallen nach 30 Tagen. `KORE_INSTANCE_ID` und
`KORE_PLAN_ORIGIN_PRINCIPAL` änderst du nie, sobald Daten da sind.

### Was die Bridge darf, und was nicht

| Sie darf | Sie darf nicht |
| --- | --- |
| `hermes` für genau das eine Profil aus `KORE_HERMES_PROFILE` starten, als ihr Dienstnutzer, mit dem Prompt über stdin | im App-Chat Terminal, Dateien, Code-Ausführung, Cronjobs, Computer- oder Browser-Steuerung freigeben; die Werkzeuge kommen aus einer festen Liste (`-t`), nicht aus einer Sperrliste |
| im allgemeinen Chat `web,skills,memory` erlauben, im Health-Chat mit Herkunftsticket `skills,memory,kore`, wo Health-Kontext kein Ticket hat (Tages-Anpassung) `skills,memory`; im Health-Chat `web,skills,memory,kore` (ohne Ticket `web,skills,memory`) nur, wenn die Anfrage `health_web_allowed: true` sagt | `web` ohne diese ausdrückliche Anfrage in die Nähe von Health-Kontext lassen, der Tages-Anpassung in keinem Fall `web` geben oder Dienst-Geheimnisse an Hermes vererben: Das Kind bekommt nur `HOME`, `PATH`, sieben Schlüssel wie `LANG`/`TZ` und, wenn du `KORE_HERMES_DATA_DIR` setzt, `HERMES_HOME` |
| jeden Prompt mit der festen ersten Zeile `[KORE-APP-NACHRICHT]` beginnen und jeden Hermes-Lauf in einem frischen, leeren temporären Ordner starten, der danach entfernt wird | eine Nachricht, die mit einem Dateipfad beginnt, diese Datei anhängen lassen oder Hermes `AGENTS.md`, `CLAUDE.md` und ähnliche Kontextdateien aus dem Home-Verzeichnis in den Prompt laden lassen |
| das angepasste Training des Tages nur zurückgeben, wenn es leichter oder kürzer ist als das, was die App geschickt hat, und oben in der Session nur mit `session_id`, `name` und `exercises` | ein schwereres oder längeres zurückgeben (mehr Sätze, Gewicht, Wiederholungen, längere Dauer, neue oder umbenannte Übungen): Die Antwort wird verworfen, die App bekommt 502 mit einem festen Satz |
| Sessions der Quellen `kore-general`/`kore-health` lesen, um Titel und Vorgänger zu finden, und sie auf Wunsch oder nach 30 Tagen löschen, angeheftete und archivierte eingeschlossen; die Quelle jeder Session wird vor dem Löschen geprüft, auch beim Vergessen eines ganzen Scopes und beim Verfall | Sessions anderer Quellen anfassen oder das Gedächtnis oder die Konfiguration des Profils ändern |
| bei einem fehlgeschlagenen Turn eine Zeile `kore-turn-failed` (Klasse, Code, Exit) ins Journal schreiben | Prompt, Antwort, Token, Health-Werte oder Hermes-stderr ins Journal oder an die App geben |
| höchstens 2 Hermes-Läufe gleichzeitig ausführen, auf welchem Weg auch immer sie kommen (Hintergrund-Turn, synchroner Chat, Tages-Anpassung; ein dritter bekommt 429 `busy`), und 64 Antworten bis zu 24 Stunden im Arbeitsspeicher halten | ohne Token (mindestens 32 Zeichen), Origin-Geheimnis und Principal starten, mit `skills.inline_shell` an starten oder Hermes laufen lassen (geprüft beim Start und vor jedem Lauf), auf einer öffentlichen Adresse lauschen oder per `sudo`/setuid mehr Rechte bekommen (`NoNewPrivileges=yes`) |

Feste Grenzen: Anfrage höchstens 256 KiB, Prompt 120 KiB, Hermes-Ausgabe 512 KiB, Laufzeit 150 s;
höchstens 64 Verbindungen gleichzeitig (weitere werden ohne Antwort geschlossen), 20 s ab
Verbindungsbeginn für Kopfzeilen und Body (zusätzlich zum 30-s-Socket-Timeout, damit ein langsamer
Absender keinen Thread festhalten kann); die Ingest-API nimmt höchstens 16 MiB an, und nur mit
gültigem Token. Ingest und MCP beantworten jede weitere Anfrage mit 503, sobald 1024 Verbindungen offen
sind oder 1024 Anfragen laufen, und schließen eine ruhende Keep-Alive-Verbindung nach 5 s (uvicorn hat
keine Frist für Kopfzeilen oder Body, siehe [Restrisiken](#was-bleibt-restrisiken)). Tokens werden in
allen drei Diensten zeitkonstant verglichen; der MCP-Server vergleicht SHA-256-Digests gleicher Länge,
sodass nicht einmal die Länge des Tokens im Zeitverhalten steckt.

### Was bleibt (Restrisiken)

Kore schickt Health-Kontext nur in Chats mit Health-Freigabe, und die Bridge gibt das kore-MCP nur
dort frei. Was dein Bot danach damit tut, steuert Kore nicht:

- **Dein Modell-Anbieter sieht alles, was im Prompt steht**, im Health-Chat also den
  Health-Kontext. Welcher Anbieter das ist, bestimmst du im Profil.
- **Gedächtnis.** Der Bot kann sich mit `memory` etwas aus einem Health-Chat notieren. Solche Notizen
  fließen in jede spätere Session des Profils ein, auch in allgemeine App-Chats und in andere Kanäle
  desselben Profils (etwa Telegram). Löschen und Verfall in Kore erreichen sie nicht; dieses
  Gedächtnis verwaltest du in Hermes.
- **Das Gedächtnis ist ein Weg in zwei Schritten vom Health-Chat zur Websuche.** `memory` bleibt
  absichtlich unter den Werkzeugen eines Health-Chats, damit sich der Coach merken kann, was er für
  den nächsten Chat braucht. Damit kann ein Health-Chat Gesundheitsfakten ins Gedächtnis des Profils
  schreiben, und ein allgemeiner Chat, der die Websuche hat, liest dasselbe Gedächtnis. Theoretisch
  könnte eine manipulierte Webseite deshalb einen allgemeinen Chat dazu bringen, früher gemerkte
  Gesundheitsfakten nach draußen zu schicken: erst füllt sich das Gedächtnis in einem Health-Chat,
  dann lenkt eine Webseite einen späteren allgemeinen Chat. Der Health-Chat selbst hat kein `web`,
  solange du es nicht einschaltest (nächster Punkt). Was hilft: Halte sensible Werte aus dem heraus, was der Coach speichert (bitte ihn, sie sich nicht
  zu merken), sieh das Gedächtnis des Profils ab und zu durch und lösche, was nicht bleiben soll, und
  nimm für Kore ein eigenes Profil, damit nichts aus deinen anderen Kanälen hineinfließt. Health- und
  allgemeiner Chat von Kore teilen sich dieses Profil, ein eigenes Profil trennt sie also nicht
  voneinander.
- **Websuche im Health-Chat, wenn du sie einschaltest.** Das ist deine ausdrückliche Entscheidung in
  der App, auf eigenes Risiko, und standardmäßig aus. Ist sie an, stehen der Health-Kontext im Prompt
  und die `kore`-Werkzeuge neben einem Werkzeug, das Adressen abrufen kann: Eine manipulierte
  Webseite, die der Coach findet oder liest, könnte ihn dazu bringen, Gesundheitswerte an eine Adresse
  ihrer Wahl zu schicken, und die Bridge kann das nicht verhindern. Schaltest du sie mitten im Chat
  ein, liegt mehr offen als die aktuelle Nachricht: Der Lauf mit Websuche setzt dieselbe Session fort,
  also hat der Coach auch die früheren Turns dieses Health-Chats samt ihrem Health-Kontext vor sich.
  Beginne für eine Frage, die das Web braucht, einen neuen Chat, wenn die früheren draußen bleiben
  sollen. Als weiche Maßnahme, nicht als Sperre, hängt die Bridge dem Prompt eines solchen Turns am
  Ende einen festen Satz an (`[KORE-WEBSUCHE — BRIDGE-AUTORITATIV]`: keine Gesundheitswerte, Namen,
  Notizen oder Planinhalte in Suchanfragen oder URLs, nur allgemein suchen); ein manipulierter Text
  kann den Coach davon abbringen.
  Schalte sie nur für eine Frage ein, die sie braucht, und danach wieder aus. Die Tages-Anpassung hat
  nie Websuche, was auch immer die App schickt (auf das Feld antwortet sie mit `400`).
- **Externer Gedächtnis-Anbieter.** Nutzt das Profil einen, bringt `memory` dessen Werkzeuge mit, und
  Turns samt Health-Kontext können dorthin gehen. Prüfen mit `hermes -p <profil> memory status`.
- **Skills.** `skill_manage` gehört zu `skills`. Der Bot kann damit Skills des Profils anlegen oder
  ändern, auch wenn ihn ein Text aus der Websuche dazu verleitet. Ein solcher Skill wirkt in allen
  Kanälen des Profils weiter. Sieh dir neue oder geänderte Skills an, wenn dir etwas seltsam vorkommt.
  Shell-Befehle in Skills (`skills.inline_shell`) müssen aus bleiben, siehe Schritt 5 der Einrichtung.
  Die Bridge prüft das beim Start und vor jedem Hermes-Lauf erneut, aber die Prüfung deckt weder das
  verwaltete Konfigurations-Overlay von Hermes ab noch die ältere Sicherung, auf die Hermes bei einer
  kaputten `config.yaml` zurückfällt. Lass nach Änderungen am Profil `kore-doctor` laufen.
- **Suche in früheren Chats.** Im App-Chat fehlt `session_search`. Andere Kanäle desselben Profils
  können Kore-Sessions mit ihrer eigenen Suche aber finden, bis sie gelöscht werden oder verfallen. Ein
  eigenes Profil nur für Kore vermeidet das.
- **Gestoppte oder verworfene Anfragen.** Ein Stopp beendet Hermes, aber was bis dahin beim Anbieter
  und in der Session angekommen ist, bleibt dort bis zum Löschen oder Verfall (30 Tage).
- **Auto-Titel.** Ist `auxiliary.title_generation` an, sieht das Titel-Modell die erste Nachricht samt
  Health-Kontext. Die Beispiel-Konfiguration schaltet ihn aus.
- **Plugins, Hooks und `SOUL.md` des Profils** laufen mit (kein `--safe-mode`). Sie sind deine
  Konfiguration, nicht die des Chats; die Werkzeuge bleiben durch die `-t`-Liste begrenzt.
- **Ein neues Hermes-Werkzeugset** könnte nach einem Update unter einen erlaubten Namen fallen. Lass
  nach jedem Hermes-Update `kore-doctor` laufen: Er meldet jedes Terminal-, Datei-, Code-, Cron-,
  Computer- oder Browser-Werkzeug in den App-Chat-Listen. Dein Agent sollte ohnehin nicht als root,
  nicht mit passwortlosem `sudo` und nicht in der Gruppe `docker` laufen.
- **Kopplungszustand auf der Platte.** `devices.json` (nur Hashes der Gerätetokens) ist für die Gruppe
  lesbar, die du Docker gegeben hast (`KORE_STATE_GID`). Hashes von 256-Bit-Tokens lassen sich nicht
  zu Tokens zurückrechnen; behandle trotzdem alle in dieser Gruppe als vertrauenswürdig.
- **Nichts bremst das Raten eines Bearer-Tokens.** Nur der Kopplungscode hat eine Versuchsgrenze. Die
  statischen und die Gerätetokens schützen ihre Länge (mindestens 32 Zeichen, zufällig) und die
  Tatsache, dass die Dienste nur in deinem Heimnetz oder VPN erreichbar sind.
- **Die Verbindungsgrenze gilt global, nicht je Adresse.** Wer in deinem Netz sitzt, kann die 64
  Verbindungen der Bridge je bis zu 20 s belegen. Das sperrt die Bridge vorübergehend, bindet aber
  keine unbegrenzten Threads oder Speicher.
- **Ingest und MCP haben keine Frist für Kopfzeilen oder Body.** uvicorn 0.54, das beide bedient,
  bietet keine an. Beide beantworten jede weitere Anfrage mit 503, sobald 1024 Verbindungen offen sind
  oder 1024 Anfragen laufen, und schließen eine ruhende Keep-Alive-Verbindung nach 5 s. Wer in deinem
  Netz seine Kopfzeilen aber tröpfchenweise schickt, hält eine solche Verbindung beliebig lange offen,
  und 1024 davon sperren die Ingest-API (der MCP-Server lauscht nur auf Loopback), bis sie schließen.
  Der Speicher je Verbindung bleibt klein; die systemd-Units begrenzen außerdem die offenen Dateien
  auf 4096.
- **Angemeldete Uploads kosten weiter Speicher.** Mit gültigem Token braucht ein 16-MiB-Snapshot beim
  Parsen in der Ingest-API grob 400 bis 500 MiB; die Speichergrenze von Container oder Unit deckelt
  einen Ausreißer. Ohne gültiges Token wird nichts gelesen.
- **Andere Oberflächen desselben Linux-Nutzers** können die Geheimnis-Dateien lesen, siehe
  [Netz](#netz).

## Daten und Datenschutz

Deine Daten bleiben auf **deinem** Server. Kore schickt deine Gesundheitsdaten nur an den Server, den
du koppelst, und dieses Repo enthält weder Analyse noch Telemetrie noch eigene ausgehende Aufrufe.
FastMCPs eigene Abfrage bei PyPI nach einer neueren Version beim Start ist abgeschaltet
(`FASTMCP_CHECK_FOR_UPDATES=off`, gesetzt von `mcp_server.py` selbst sowie in der Compose-Datei und der
systemd-Unit). Was jeder Teil speichert:

| Wo | Was |
| --- | --- |
| **Ingest + MCP**, Docker-Volume `kore-db` (SQLite) | der jüngste Snapshot aus der App: Profil-Basisdaten, Erholung, HRV-Tageswerte bis 30 Tage, Ruhepuls, Schlaf, Workouts mit Puls und kcal, tägliche Schritte, Gewicht und Körperfett (bis 30 Tage), Ernährung als Tagessummen (bis 30 Tage; nur wenn du sie in Kore teilst), Trainingspläne mit Namen und Notizen; offene Plan-Vorschläge |
| **Kopplungszustand**, `state/` neben der `docker-compose.yml` | die Server-ID, der Hash eines offenen Einmalcodes samt Zahl der Fehlversuche und je gekoppeltem Gerät eine ID, ein Name, der Hash seines Tokens und zwei Zeitstempel. Keine Tokens oder Codes im Klartext |
| **Coach-Bridge** | keine Datenbank. Laufende Turns liegen im Arbeitsspeicher (bis 24 Stunden, 64 Antworten) und gehen beim Neustart verloren. Im Zustandsordner führt `bridge-sessions.json` die Hermes-Sessions, die die Bridge angelegt hat, je mit Scope und letzter Nutzung (keine Inhalte, keine Chat-IDs), damit sie sie löschen kann; `bridge-hermes-options.json` merkt sich, welche Optionen das installierte Hermes ablehnt. Jeder Hermes-Lauf arbeitet in einem leeren Ordner (`kore-bridge-work/chat` im Datenordner von Hermes), der vor und nach dem Lauf geleert wird. Das Journal bekommt nur eine Fehlerzeile ohne Inhalt |
| **Hermes-Profil**, `~/.hermes/profiles/<profil>/` | die Chat-Sessions von Kore (seit Hermes v0.22.5 mit der Gateway-Quelle `oneshot`, davor `kore-general`/`kore-health`) und das Gedächtnis des Profils. Die Bridge löscht Kore-Sessions nach 30 Tagen ohne Aktivität und wenn du einen Chat in Kore löschst; das Gedächtnis fasst sie nie an |
| **Dein Modell-Anbieter** | die Prompts, und bei eingeschaltetem „Health-Kontext mit Coach teilen“ der Health-Kontext in jeder Nachricht. So funktioniert ein Chat; wähl deinen Anbieter entsprechend |

Ohne den Schalter „Health-Kontext teilen“ bekommt der Coach nur, was du tippst. Schritte, Gewicht und
Körperfett nutzen den Health-Zugriff, den Kore schon hat; Ernährung braucht einen eigenen Schalter in den
Experimentellen Funktionen von Kore (Standard aus, sichtbar, sobald ein Coach verbunden ist) und eine
eigene Health-Berechtigung, und Ausschalten leert sie beim nächsten Sync auf deinem Server. Der Ingest
schreibt keine Snapshot-Werte ins Log und gibt sie in Fehlern nicht zurück.

## Wenn etwas nicht geht

Kore nennt unter Experimentelle Funktionen den nächsten Schritt. Die Bridge prüfst du dort mit
**„Coach-Bridge testen“**.

| Kore oder der Server sagt | Was wirklich los ist | Was du tust |
| --- | --- | --- |
| „Der Server antwortet nicht. Läuft `docker compose up`?“ | Es kam überhaupt keine Antwort: Der Dienst ist aus, oder die Adresse zeigt woandershin. | `docker compose ps`. Läuft er, dann `KORE_BIND_ADDRESS` prüfen: Steht dort `127.0.0.1`, erreicht ihn nur dieser Rechner, nicht dein iPhone. |
| „Token abgelehnt. Prüfe den Ingest-Token in .env.“ | Der Server hat geantwortet und dich abgewiesen (401/403). Die Adresse stimmt. | Neu koppeln oder `KORE_INGEST_TOKEN` mit dem Feld in Kore vergleichen; nach einer Änderung `docker compose up -d`. Ein widerrufenes Gerät wird genauso abgewiesen. |
| „Coach-Bridge nicht erreichbar. Läuft der Dienst kore-coach-bridge, und hat Kore Zugriff aufs lokale Netzwerk?“ | Die Bridge ist ein **eigener** Dienst, nicht Teil von Compose; oder iOS hat Kore den Zugriff aufs lokale Netzwerk nicht erlaubt. | `systemctl status kore-coach-bridge`, oder `python3 coach-bridge/bridge.py` im Vordergrund starten und die erste Zeile lesen. Startet sie nicht, nennt die Zeile den fehlenden oder noch auf `change-me` stehenden Schlüssel. Auf dem iPhone: Einstellungen › Kore › Lokales Netzwerk. Unterwegs: Ist das VPN an? |
| „Coach-Token abgelehnt. Prüfe KORE_BRIDGE_TOKEN in bridge.env und füge ihn ohne „Bearer“ ein.“ | Die Bridge hat geantwortet und dich abgewiesen. | Neu koppeln oder `KORE_BRIDGE_TOKEN` ohne das Wort „Bearer“ einfügen. |
| „Die Bridge läuft, ist aber nicht fertig eingerichtet (503).“ | Sie antwortet, hat aber kein Token; absichtlich ein Fehler statt eines offenen Zugangs. | `KORE_BRIDGE_TOKEN` in `bridge.env` setzen und neu starten. |
| „Deine Coach-Bridge ist zu alt (API 1, nötig 2). Aktualisiere kore-server.“ | Die Bridge spricht eine ältere Protokollversion. | kore-server auf den Stand holen, den Kore nennt, und die Bridge neu starten. |
| **„Coach-Bridge testen“** in Kore sagt, der Server sei älter als die App | Der Ingest nennt ein Snapshot-Feld nicht, das die App kennt: `GET /capabilities` antwortet 404 (ein Ingest v0.13.0) oder ohne `activity_body_nutrition_v1` bzw. `apple_hrv_v1`. Nichts ist kaputt: Kore synchronisiert weiter und lässt weg, was der Server nicht annimmt (Schritte, Gewicht, Ernährung, die Apple-Kennungen der Erholungs-HRV). | Den Server auf den Stand holen, den Kore nennt, siehe [Aktualisieren](#aktualisieren). |
| „Deiner Coach-Bridge fehlt: …“ | Die Bridge meldet eine Fähigkeit nicht, die Kore verlangt; der Name steht im Text. | kore-server aktualisieren. Fehlt `plan_origin_v1`, zusätzlich `KORE_PLAN_ORIGIN_SECRET` und `KORE_PLAN_ORIGIN_PRINCIPAL` in `bridge.env` prüfen; beide müssen gesetzt sein. Fehlt `device_pairing_v1`, ist der Zustandsordner unbrauchbar (siehe die beiden Kopplungs-Zeilen unten). |
| Kopplung scheitert (401 `pairing_failed`) | Der Code ist falsch, abgelaufen (10 Minuten), schon benutzt, nach 1000 Fehlversuchen tot, oder die Kopplung ist auf dem Server nicht verfügbar. | `kore-doctor pair` erneut ausführen und einen frischen Link nehmen. Klappt es weiter nicht: `journalctl -u kore-coach-bridge` und nach `Device pairing unavailable` suchen. |
| Kopplung scheitert immer wieder mit 401, obwohl der Link frisch ist | 1000 Fehlversuche mit einem wohlgeformten Code töten diesen Code; etwas in deinem Netz hat ihn immer wieder probiert. Eine Anfrage, die kein wohlgeformter Code ist, zählt nicht. | Mit `kore-doctor pair` einen neuen Code erzeugen (ein toter Code kommt nicht zurück) und nach dem Gerät suchen, das weiter sendet: Das Journal nennt die Adresse jedes Fehlversuchs. |
| Kopplung: 429 `rate_limited` | Fünf Fehlversuche innerhalb einer Minute von einer Adresse. | Eine Minute warten und einen neuen Code nehmen. |
| Journal: `Device pairing unavailable: …` | Der Zustandsordner fehlt, oder die Bridge kann nicht hineinschreiben. | `kore-doctor init-state`, dann die Besitzer prüfen. Mit der gehärteten Unit muss `<HOME>/kore-server/state` in `ReadWritePaths` stehen. |
| `docker compose up` bricht mit „set KORE_STATE_GID …“ ab | In der `.env` fehlt `KORE_STATE_GID`. | `kore-doctor init-state` ausführen und die ausgegebene Zeile in die `.env` übernehmen. |
| `docker compose up` bricht mit „set KORE_… in .env“ für einen anderen Schlüssel ab | Compose braucht diesen Schlüssel für einen Container und findet keinen. | Den Schlüssel in der `.env` ergänzen (mit der `.env.example` vergleichen). |
| `docker compose up` bricht mit „port is already allocated“ ab (8081 oder 8000) | Ein anderes Programm oder ein zweiter Kore-Stapel nutzt diesen Host-Port schon. | `KORE_INGEST_PORT` und/oder `KORE_MCP_PORT` in der `.env` auf freie Ports setzen, `docker compose up -d`; den neuen Port in der Server-Adresse in Kore (Kopplungslinks folgen ihm) und in der MCP-`url` des Profils verwenden. |
| `hermes mcp test kore` scheitert mit `No module named 'mcp'` | Dein Hermes wurde ohne sein MCP-Extra installiert; die Ursache liegt bei Hermes. | `hermes pm install --extra mcp` als der Nutzer (oder im Container), der Hermes betreibt; `kore-doctor` nennt dieselbe Abhilfe. |
| Ein Dienst startet nicht, die Meldung lautet „is too short: at least 32 characters are required“ (Ingest, MCP) oder „KORE_BRIDGE_TOKEN must contain at least 32 characters“ (Bridge) | Seit 0.13 brauchen die drei Bearer-Tokens 32 Zeichen. | Mit `openssl rand -hex 24` ein neues erzeugen, eintragen, neu starten und die Gegenstelle nachziehen (`.env` des Hermes-Profils, oder Kore auf dem Experten-Weg). |
| Der Ingest-Container startet in einer Schleife neu; das Log sagt „… must be a concrete IPv4 loopback, RFC 1918 or VPN (100.64.0.0/10) address“ | `KORE_BIND_ADDRESS` ist `0.0.0.0`, ein Hostname oder eine öffentliche Adresse. | `docker compose logs ingest`; die LAN-Adresse dieses Rechners in `KORE_BIND_ADDRESS` eintragen, dann `docker compose up -d`. Dieselbe Meldung von `python ingest_api.py` oder `python mcp_server.py` ohne Docker nennt `KORE_INGEST_HOST`, `KORE_BIND_ADDRESS` oder `KORE_MCP_HOST`. |
| Die Bridge startet nicht und nennt `skills.inline_shell` | Die `config.yaml` des Profils hat Shell-Befehle in Skills an oder nicht eindeutig aus; ein gequoteter, escapter oder Flow-Schlüssel irgendwo in der Datei gilt als nicht eindeutig aus. | Unter `skills:` genau `inline_shell: false` setzen (oder den Schlüssel entfernen) und nur schlichte Schlüssel verwenden, siehe Schritt 5 der Einrichtung, und neu starten. |
| Coach-Aufrufe scheitern mit 503; im Journal steht `kore-profile-unsafe skills.inline_shell …` | Die `config.yaml` des Profils wurde geändert, während die Bridge läuft, und ist nicht mehr eindeutig sicher. Bis sie es wieder ist, startet die Bridge keinen Hermes-Lauf. | Die Datei wie in der Zeile darüber korrigieren; der nächste Aufruf klappt ohne Neustart. `kore-doctor` zeigt das Urteil. |
| Kore zeigt „Die Bridge ist gerade ausgelastet.“ (429 `busy`) | Zwei Hermes-Läufe laufen schon (ein Chat, ein Hintergrund-Turn oder eine Tages-Anpassung); die Bridge nimmt keinen dritten an. Ein gestoppter Turn behält seinen Platz, bis Hermes wirklich beendet ist. | Einen Moment warten und es erneut versuchen; ein Lauf endet spätestens nach 150 s. |
| Die Tages-Anpassung zeigt „Die Anpassung des Coachs steigert die Session; erlaubt ist nur Reduzieren.“ (502 `escalation`) | Die Antwort des Coachs würde die Session schwerer oder länger machen oder Übungen hinzufügen oder umbenennen. Die Bridge verwirft sie. | Nichts ist kaputt; erneut versuchen. Passiert es immer wieder, sieh dir die Anweisungen in der `SOUL.md` des Profils an. |
| Journal der Bridge: `fork failed`, `Too many open files` oder ein OOM-Kill | `TasksMax`, `LimitNOFILE` oder `MemoryMax` der Unit sind für deine Hermes-Läufe zu niedrig. | Den Wert in `kore-coach-bridge.service` erhöhen, `sudo systemctl daemon-reload`, neu starten. |
| Gekoppelt, aber die Ingest-API antwortet 401 | Der Ingest kann `devices.json` nicht lesen: Die Gruppe passt nicht. | `kore-doctor` hat einen Check „Zustandsordner“; `KORE_STATE_GID` auf die Gruppe des Ordners setzen und `docker compose up -d --force-recreate` ausführen. |
| `kore-doctor pair` lehnt eine Adresse ab | Akzeptiert werden private IPv4-Adressen und `https://<name>.ts.net`, nicht `127.0.0.1`, andere Hostnamen, `http` auf einem Namen oder auf einer Tailscale-Adresse (100.x) oder öffentliche Adressen. | `KORE_BRIDGE_BIND_HOST` und `KORE_BIND_ADDRESS` auf die Heimnetz-Adresse setzen, die dein iPhone benutzt, oder `--bridge-url` und `--ingest-url` übergeben (bei Tailscale: die `https://<name>.ts.net`-Namen). |
| „Die Bridge hat unvollständig geantwortet.“ | Es kam eine Antwort, aber keine lesbare. | Zeigt die Adresse wirklich auf die Bridge (Port 8090) und nicht auf einen anderen Dienst? Dann `journalctl -u kore-coach-bridge`. |
| „Achtung: Deine Bridge übergibt Prompts über die Kommandozeile. Entferne KORE_HERMES_ALLOW_ARGV_PROMPTS aus bridge.env.“ | Die Bridge ist bereit, reicht den Prompt aber über `argv` weiter (Prinzip 6). | Die Zeile aus `bridge.env` löschen und die Bridge neu starten. |
| „Der Server hat mit Fehler 500 geantwortet.“ | Etwas im Server selbst. | `docker compose logs -f ingest`; die Statuszahl ist das, womit du im Log suchst. |
| Ein Chat scheitert mit `hermes_failed`, obwohl `hermes` in deiner Shell läuft | Die Bridge startet Hermes mit einem minimalen `PATH`; dein Modell-Anbieter braucht ein Programm außerhalb davon. | Den Ordner dieses Programms vorn in `KORE_HERMES_CHILD_PATH` in `bridge.env` eintragen; `kore-doctor` prüft das. |

Zwei Dinge sind, was sie sind: Die **401** auf `127.0.0.1:8000/mcp` ist korrekt (der Port lebt und
weist Fremde ab), und **„Verbunden“ heißt nicht „antwortet“**: Das sagt erst **Jetzt synchronisieren**.

## Installation prüfen: kore-doctor

```bash
python3 scripts/kore-doctor                 # als Nutzer der Bridge; Standard: ~/kore-coach-bridge/bridge.env und <repo>/.env
python3 scripts/kore-doctor --bridge-env /pfad/bridge.env --server-env /pfad/.env --ingest-url http://10.0.0.10:8081
python3 scripts/kore-doctor --bridge-only   # nur Bridge und Hermes
```

`--bridge-env`, `--server-env` und `--state-dir` gelten vor und nach einem Unterbefehl
(`kore-doctor devices --state-dir …`) und auch ohne einen.

Das Skript braucht nur die Standardbibliothek und gibt nie einen Geheimwert aus. Es prüft, dass

- der Ingest-`/health` 200 liefert (und mit fremdem Origin 403), auf `KORE_INGEST_PORT` aus der
  Server-`.env` (Standard 8081);
- der MCP-Server auf `127.0.0.1` (Port `KORE_MCP_PORT`, Standard 8000) ohne Token 401 liefert und über die
  Netzadresse nicht erreichbar ist. Ist `KORE_MCP_URL` in der Server-`.env` gesetzt (ein MCP, der nur im
  Compose-Netz liegt), überspringt der Doctor das mit einer `[HINWEIS]`-Zeile;
- die Bridge ohne Token 401 liefert und ihr `/health` mit Token jede Fähigkeit aus
  [`contract/app-required-capabilities.json`](contract/app-required-capabilities.json),
  `prompt_transport: stdin` und `app_chat_toolsets` ohne Terminal-, Datei-, Code-, Cron-, Computer-
  und Browser-Werkzeuge und im Health-Scope ohne `web` meldet (die Wunsch-Liste `health_with_web`
  darf nichts außer `web` hinzufügen);
- `KORE_BRIDGE_TOKEN` (`bridge.env`) sowie `KORE_INGEST_TOKEN` und `KORE_MCP_TOKEN` (`.env` des
  Servers) mindestens 32 Zeichen haben;
- `KORE_PLAN_ORIGIN_SECRET` (per Hash), `KORE_INSTANCE_ID` und `KORE_PLAN_ORIGIN_PRINCIPAL` in Server
  und Bridge gleich sind;
- das Profil existiert (unter `KORE_HERMES_DATA_DIR`, wenn du es setzt), `hermes -p <profil> mcp test
  kore` gelingt (Zeitlimit 30 Sekunden; scheitert es, weil Hermes das Modul `mcp` fehlt, nennt der Doctor
  `hermes pm install --extra mcp`), Hermes mit genau dem Env läuft, das ihm die Bridge gibt, und die
  `SOUL.md` des Profils die aktuelle [`kore-app-chat-soul.md`](hermes/kore-app-chat-soul.md) enthält;
- `skills.inline_shell` in der `config.yaml` des Profils aus ist (fehlt oder `false`, nur schlichte
  YAML-Schlüssel);
- er nennt den Modell-Anbieter des Profils und seine eingeschalteten Plugins; ein Programm, das ein
  Anbieter-Plugin auf dem `PATH` der Bridge nicht findet, zeigt sich als Startwarnung von Hermes und ist
  eine Abweichung;
- der Zustandsordner der Kopplung mit den richtigen Rechten existiert und `KORE_STATE_GID` seine Gruppe
  ist.

Exit 0 nur ohne jede Abweichung. `--bridge-only` lässt Ingest, MCP und Server-`.env` weg, etwa wenn sie
auf einem anderen Rechner laufen. Die weiteren Unterbefehle sind `init-state`, `pair`, `devices` und
`revoke` (siehe [Kopplung](#kopplung-mit-dem-iphone)). Die meisten Meldungen sind deutsch; der
Kopplungs-Hinweis ist zweisprachig.

## Aktualisieren

```bash
cd ~/kore-server
git fetch --tags origin
git checkout --detach <neuer Tag>     # der Tag, den Kore oder die Release-Seite nennt
git rev-parse HEAD                    # muss dem Commit-Hash dieses Tags entsprechen; sonst hier abbrechen
docker compose up -d --build
sudo systemctl restart kore-coach-bridge      # Bridge unter systemd; im Docker-Container siehe unten
python3 scripts/kore-doctor
hermes -p <profil> mcp test kore              # „Connected“ und die Werkzeuge dieser Version
```

1. **Den Tag auschecken und prüfen.** Release-Tags werden nie verschoben. Vergleiche `git rev-parse HEAD`
   mit dem Commit-Hash, den der Einrichtungsprompt von Kore oder die Release-Seite für diesen Tag nennt.
   Weicht er ab, mach nicht weiter.
2. **Neu bauen:** Ingest und MCP mit `docker compose up -d --build`.
3. **Die Bridge neu starten.** Unter systemd: `sudo systemctl restart kore-coach-bridge`. Läuft sie als
   Prozess im Hermes-Container (siehe [Hermes im Docker-Container](#hermes-im-docker-container-z-b-unraid),
   Schritt 5): beende sie und starte sie mit demselben Befehl neu.
4. **Prüfen.** `kore-doctor` muss mit 0 enden, und `hermes -p <profil> mcp test kore` muss „Connected“
   melden (ab Version 0.14: „Tools discovered: 10“).

Gekoppelte Geräte bleiben gekoppelt: Der Kopplungszustand und alle Tokens bleiben unberührt. Neue
Variablen in `.env` und `bridge.env` sind optional; ergänze eine nur, wenn du brauchst, was sie bewirkt
(`KORE_INGEST_PORT`, `KORE_MCP_PORT`, `KORE_MCP_URL` in der `.env`, `KORE_HERMES_DATA_DIR`, `KORE_HERMES_WORK_DIR` in der
`bridge.env`). Ein älterer Server funktioniert mit einem neueren Kore weiter; Kore lässt weg, was der
Server nicht annimmt, und sagt es unter **„Coach-Bridge testen“**, mit einem Link auf diesen Abschnitt
und einem Text, den du in deinen Hermes einfügen kannst, damit er diese Schritte ausführt.

Lass `kore-doctor` (und `hermes -p <profil> mcp test kore`) auch nach jedem `hermes update` laufen.
Die Änderungen je Version, mit einem Abschnitt „Upgrading from …“ je Version, stehen im
[CHANGELOG.md](CHANGELOG.md).

**Sicherheits-Härtung in 0.13.** Dienste, die früher starteten, können jetzt die Arbeit verweigern:
Tokens unter 32 Zeichen, eingeschaltetes `skills.inline_shell`, eine Bind-Adresse außerhalb von
Loopback, privaten und VPN-Bereichen. Compose gibt `.env`-Schlüssel nur weiter, wenn die
`docker-compose.yml` sie nennt, und eine selbst geschriebene systemd-Unit für die Ingest-API muss
`python ingest_api.py` statt `uvicorn` starten. Übernimm die Bridge-Unit aus dieser README neu (neue
Grenzen). Die Liste steht im [CHANGELOG](CHANGELOG.md) unter „Breaking for self-hosters“.

**Von 0.10 oder früher:** `python3 scripts/kore-doctor init-state` ausführen und die ausgegebene Zeile
`KORE_STATE_GID=…` in die `.env` übernehmen (Compose startet sonst nicht); bei der gehärteten Unit den
Zustandsordner in `ReadWritePaths` ergänzen; den Abschnitt `## Kore-App-Chat` in der `SOUL.md` des
Profils durch die aktuelle Datei ersetzen (`kore-doctor` meldet einen veralteten). Der MCP-Server ist
jetzt nur noch auf `127.0.0.1` veröffentlicht.

## Referenz

### Die Werkzeuge (9 lesen, 1 schlägt vor)

| Werkzeug | Liefert |
| --- | --- |
| `get_recovery_today` | Tageswert: Erholung, HRV (mit der tatsächlich verwendeten Methode), Ruhepuls, Schlafstunden, Bandlage |
| `get_hrv_trend(days=30)` | Je Tag ein HRV-Wert (SDNN in ms) für die letzten `days` Tage: Tageswerte, keine Messreihe innerhalb eines Tages |
| `get_sleep_summary(days=7)` | Je Nacht Gesamt-, Tief- und REM-Stunden sowie Effizienz |
| `get_recent_workouts(limit=10)` | Datum, Art, Dauer, Durchschnittspuls, Volumen |
| `get_activity_body_trend(days=30)` | Je Tag (1 bis 30 Tage, neueste zuletzt): Schritte (`count`) und Körper (`weight_kg` in kg, `body_fat_pct` in %, die letzte Messung des Tages) |
| `get_nutrition_trend(days=30)` | Je Tag (1 bis 30 Tage, neueste zuletzt): `kcal`, `protein_g`, `carbs_g`, `fat_g` als Tagessummen aus Apple Health; unvollständig, wenn nicht alles erfasst wird; leer, solange du Ernährung in Kore nicht teilst |
| `get_active_plan` | Der aktuell in der App aktive Trainingsplan |
| `get_training_plans` | Alle Pläne, plus Zeitstempel des Snapshots |
| `get_coach_briefing` | Das verdichtete Tagesbild in einem Aufruf: Erholung, HRV, Schlaf, Workouts, Profil, Pläne, dazu die letzten 7 Tage Schritte und Gewicht (`activity_body_7d`) und Ernährung (`nutrition_7d`) |
| `propose_training_plan(plan, origin_ticket)` | Legt einen Plan als `pending` ab; die App bestätigt |

Die HRV-Methode steckt im Schlüsselnamen: `hrv_rmssd_ms`, `hrv_sdnn_ms` oder `hrv_ms` bei unbekannter
Methode. Ein Wert wird nie stillschweigend als SDNN etikettiert. Ausdrücklich **nicht** geliefert wird,
was gar nicht erst auf dem Server liegt: Health-Rohwerte, Standort, Kontakte, Fotos, Zyklusdetails. Dein
Coach kann nichts löschen, nichts abhaken und nichts stillschweigend aktivieren.

Schritte, Gewicht, Körperfett und Ernährung sind neu in Snapshot v2 (Kore 0.14, Server 0.14). Sie sind für
deinen Coach nur lesbar, gehen nur an deinen eigenen Server, und eine Liste, die die App nicht schickt,
bleibt wie gespeichert. Ernährung kommt nur an, solange du sie in Kore teilst. Der Vertrag Feld für Feld
steht in [`contract/snapshot-v2.md`](contract/snapshot-v2.md).

### Plan-JSON

`propose_training_plan` nimmt einen vollständigen Endplan, hart per Pydantic validiert:

```json
{ "schema_version": "1.0",
  "name": "Woche 12, reduziert",
  "rationale": "HRV vier Nächte unter Baseline",
  "replaces_plan_id": "11111111-2222-4333-8444-555555555555",
  "replaces_plan": "Woche 12",
  "base_plan_fingerprint": "sha256:aaaaaaaa…",
  "removed_session_ids": [], "removed_exercise_ids": [],
  "sessions": [
    { "session_id": "66666666-7777-4888-8999-AAAAAAAAAAAA", "name": "Push", "kind": "strength",
      "exercises": [
        { "exercise_id": "BBBBBBBB-CCCC-4DDD-8EEE-FFFFFFFFFFFF", "name": "Bankdrücken",
          "note": "Langhantel", "sets": [{ "weight_kg": 60, "reps": 8 }] } ] } ] }
```

- Ein **neuer** Plan lässt alle Update-Felder weg. Ein **Update** braucht `replaces_plan_id`,
  `base_plan_fingerprint` und **beide** Removal-Arrays, auch leer. Die Arrays müssen nach ID-Abgleich
  exakt jede entfernte Identität aufzählen.
- `replaces_plan_id` und `base_plan_fingerprint` dürfen nur aus einem frischen, vollständigen
  App-Kontext stammen, nie aus den read-only MCP-Werkzeugen. Ein Update gegen einen veralteten Stand
  wird abgewiesen, statt still zu überschreiben.
- `kind: "cardio"` verlangt `exercises: []`; das Ziel steht in `cardio_target`.
- `valid_from` gibt es nicht und wird abgelehnt, solange Kore keine echte Terminierung hat.

Vom Vorschlag zum Plan: Der Agent ruft `propose_training_plan`; der Plan wird validiert und als
`pending` abgelegt; Kore holt ihn ab und zeigt ihn unter *Experimentelle Funktionen › Coach-Vorschläge*
mit der Kapsel „VOM COACH“, genau in der Form, in der er lokal gespeichert würde; erst dein Tippen auf
„Übernehmen“ macht ihn wirksam. Lehnst du ab, passiert nichts.

### Ingest-API

`POST /ingest` (Snapshot), `GET /plan/pending`, `POST /plan/{id}/ack` mit `{"accepted": true|false}`,
`GET /capabilities`, `GET /health` (ohne Token). `GET /capabilities` antwortet
`{"snapshot_capabilities": ["activity_body_nutrition_v1", "apple_hrv_v1"]}`: Kore liest es vor jedem
Sync und schickt die neueren Snapshot-Felder (Schritte, Gewicht, Ernährung; die Apple-Kennungen der
Erholungs-HRV) nur an einen Ingest, der sie nennt (ein Ingest v0.13.0 antwortet 404, und Kore synchronisiert
ohne sie); siehe [`contract/snapshot-v2.md`](contract/snapshot-v2.md). (Die Coach-Bridge hat ein eigenes
`GET /capabilities`, siehe unten.) Alles andere braucht `Authorization: Bearer <token>`; das Token ist
`KORE_INGEST_TOKEN` oder das Token eines gekoppelten Geräts. Das Token wird geprüft, bevor der Body
gelesen wird: Ein fehlendes oder falsches Token (oder zwei `Authorization`-Header) bekommt `401` mit
`Connection: close`, und es wird kein Body geparst. Offen ist nur der exakte Pfad `/health` (er nimmt
höchstens 4 KiB an); jeder andere Pfad, auch unbekannte und Varianten wie `/ingest%0A`, bekommt ohne
Token `401`. Die API hat kein `/docs`, `/redoc` und kein `/openapi.json`. Auch der MCP-Server weist eine
Anfrage mit zwei `Authorization`-Headern mit `401` ab, bevor er ihren Body liest.

### Protokoll der Coach-Bridge

Die Bridge ist reines Python ohne Abhängigkeiten und ruft die `hermes`-CLI als begrenzten
Unterprozess auf (`hermes -p <profil> chat -Q --format stream-json --query-file - --source
kore-<scope> -t <Werkzeugliste> --run-budget <s> [--resume <id>]`) in einem leeren temporären
Arbeitsordner, mit einem Prompt, dessen erste Zeile immer `[KORE-APP-NACHRICHT]` ist. Sie ist bewusst
Hermes-spezifisch, getestet gegen Hermes Agent v0.21.4 und v0.21.5. Sie liest das stream-json von
stdout; Antwort und Session kommen aus dem `result`-Record, nie von stderr. Die Fixtures in
`coach-bridge/fixtures/hermes-v0.21.4/` stammen aus echten Läufen, mit Platzhaltern für Modellname und
Gedächtnis-Anbieter; nach einem Hermes-Update `test_bridge.py` und `test_runner_stream_json.py`
laufen lassen.

Falls du eine eigene Bridge für einen anderen Agenten schreibst, ist dies das kleine Protokoll, das die
App spricht:

- `GET /health`, `GET /capabilities`: Fähigkeitsliste, `api_version: 2` und das optionale
  `"coach_name"` (Text mit 1 bis 24 Zeichen, nur vorhanden, wenn `KORE_COACH_NAME` gültig ist).
- `POST /pair` (ohne Bearer) und `POST /unpair` (Gerätetoken): siehe
  [`contract/pairing-v1.md`](contract/pairing-v1.md).
- `POST /coach` mit
  `{message, conversation_id, session?, context?, health_context_allowed, health_web_allowed?}`.
  `conversation_id` ist eine kanonische kleingeschriebene UUID. `session` ist eine von der Bridge
  signierte Referenz `general:<id>.<sig>` bzw. `health:<id>.<sig>`, gebunden an Scope und Chat; passt
  sie nicht oder kennt Hermes die Session nicht mehr, beginnt der Kontext neu (`context: "reset"`)
  statt eines Fehlers. Ein Gesundheitskontext wird nur weitergereicht, wenn `health_context_allowed`
  wörtlich `true` ist (fail-closed). Das optionale `health_web_allowed` (Fähigkeit
  `health_web_opt_in_v1`, Einzelheiten in
  [`contract/health-web-opt-in-v1.md`](contract/health-web-opt-in-v1.md)) muss ein JSON-Boolean sein,
  sonst `400`. Nur `true` im Health-Scope nimmt `web` in die Werkzeuge dieses Turns auf und hängt den
  festen Satz `[KORE-WEBSUCHE …]` ans Ende seines Prompts; im allgemeinen Scope bleibt es ohne
  Wirkung, und ein Wechsel zwischen den Turns eines Chats behält die Session.
- Mit zusätzlichem `client_message_id` (kanonische UUID) startet `POST /coach` einen Turn-Auftrag und
  antwortet sofort `202 {turn_id, status}`; dieselbe `client_message_id` im selben Chat liefert
  denselben Turn. Läuft im Chat schon ein Turn: `409`. Hermes-Läufe sind für die ganze Bridge auf zwei
  gleichzeitige begrenzt, auf welchem Weg auch immer sie kommen; ein dritter bekommt
  `429 {"error": …, "code": "busy"}`, auch bei einem synchronen `POST /coach` (ohne
  `client_message_id`) und bei `POST /coach/adjust-today`.
- `GET /coach/turns/{turn_id}/events`: Server-Sent Events mit `id:`; `Last-Event-ID` spielt spätere
  Ereignisse erneut ab, alle 15 s `: keepalive`. Nur Text-Deltas `{"delta": …}`, am Ende
  `{"done": true, "used_kore": …, "context": …, "session"?: …, "title"?: …}` oder
  `{"error": …, "code": …}`; Werkzeugaufrufe bleiben in der Bridge.
- `GET /coach/turns/{turn_id}` liefert Stand bzw. Ergebnis (ein fertiger Turn wird mit dieser Antwort
  gelöscht); `POST /coach/turns/{turn_id}/cancel` beendet Hermes, idempotent. Der Puffer liegt nur im
  Arbeitsspeicher, höchstens 24 Stunden.
- `POST /coach/adjust-today`: JSON-Antwort mit dem angepassten Training des Tages. Es bekommt nie
  Websuche: Eine Anfrage, die `health_web_allowed` überhaupt enthält, ist `400`. Erlaubt ist nur
  Reduzieren: Hat die Antwort des Coachs mehr Sätze, mehr Gewicht, mehr Wiederholungen oder eine
  längere Dauer, eine neue oder umbenannte Übung oder eine andere `session_id`, kommt nichts davon
  zurück, sondern
  `502 {"error": "Die Anpassung des Coachs steigert die Session; erlaubt ist nur Reduzieren.", "code": "escalation"}`.
  Übungen oder Sätze weglassen, leichtere Gewichte, weniger Wiederholungen und eine geänderte Notiz
  sind in Ordnung. Die zurückgegebene `session` trägt oben nur `session_id`, `name` und `exercises`;
  was der Coach dort sonst hinschreibt, fällt weg.
- `POST /coach/forget` mit `{conversation_id, sessions: [<signierte Referenz>, …]}` oder
  `{scope: "health" | "all"}`: stoppt laufende Turns, löscht ihre Puffer und dann die Hermes-Sessions
  samt Kompressions-Vorgängern. Antwort `{deleted, not_found, failed}`; bestätigt ist das Löschen nur
  bei `failed: 0`. Vor dem Löschen einer Session wird ihre Quelle gelesen: Nur eine Session der Quelle
  `kore-<scope>` wird gelöscht, alles andere zählt als `failed`. Angeheftete und archivierte Sessions
  sind eingeschlossen (`sessions prune --dry-run --include-archived --include-pinned`). Eine offene
  Session, die Hermes in seiner Liste verbirgt (archiviert oder versteckt), lässt sich nicht über ihre
  ID finden; das Vergessen eines Scopes zählt sie als `failed`, statt zu bestätigen. Bei sehr vielen
  Sessions kann das 120-s-Budget zuerst enden; der Rest zählt als `failed`, und der Verfall holt nach.
- Verfall: Kore-Sessions ohne Aktivität seit `KORE_BRIDGE_SESSION_RETENTION_DAYS` Tagen (Standard und
  Obergrenze 30), angeheftete und archivierte eingeschlossen, löscht die Bridge beim Start und danach
  täglich, nie während ein Turn läuft, und mit derselben Quellenprüfung.
- Ohne gültiges `Authorization: Bearer` (statisches Bridge-Token oder Gerätetoken) oder mit zwei
  `Authorization`-Headern: 401, bevor vom Body etwas gelesen wird. Eine
  unfertige Installation ist ein Fehler, nie ein offener Zugang: Ist `KORE_BRIDGE_TOKEN`,
  `KORE_PLAN_ORIGIN_SECRET` oder `KORE_PLAN_ORIGIN_PRINCIPAL` leer oder steht noch auf `change-me…`,
  startet die Bridge nicht und nennt den Schlüssel (nie den Wert). Sie startet auch nicht mit einem
  `KORE_BRIDGE_TOKEN` unter 32 Zeichen oder mit `skills.inline_shell` an oder nicht eindeutig aus in
  der `config.yaml` des Profils (die Meldung nennt Einstellung und Datei). Dieselbe Prüfung läuft vor
  jedem Hermes-Lauf: Ist die Datei inzwischen unsicher, bekommt ein Coach-Aufruf
  `503 {"error": …, "code": "profile_unsafe"}` (ein Turn-Start vor seinem `202`; ein schon
  angenommener Hintergrund-Turn endet mit `hermes_failed`).
- Werkzeuge per `-t`-Liste statt `--safe-mode` (`/health` meldet sie unter `app_chat_toolsets`):
  allgemein `web,skills,memory`; Health `skills,memory,kore`, ohne `web`. Health-Kontext ohne
  Herkunftsticket (Tages-Anpassung) bekommt `skills,memory`: kein kore-MCP, kein `web`. Nur bei
  `health_web_allowed: true` bekommt ein Health-Chat `web,skills,memory,kore` (ohne Ticket
  `web,skills,memory`); `/health` meldet diese Liste als `app_chat_toolsets.health_with_web`. Nie
  Terminal, Dateien, Code-Ausführung, Cronjobs, Computer- oder Browser-Steuerung.
- Grenzen des HTTP-Servers selbst: höchstens 64 Verbindungen gleichzeitig (weitere werden ohne
  Antwort geschlossen) und 20 s ab Verbindungsbeginn für Kopfzeilen und Body.

### Andere MCP-Clients

Der MCP-Server ist nur auf `127.0.0.1` erreichbar. Ein anderer MCP-Client (etwa eine Desktop-Chat-App)
funktioniert nur, wenn er auf demselben Rechner läuft oder du selbst einen SSH-Tunnel auf Port 8000
einrichtest. Connectoren, die eine öffentliche HTTPS-Adresse und OAuth brauchen (etwa die
Web-Connectoren gehosteter Assistenten), sind eine andere Betriebsart mit anderem Risiko und bewusst
nicht Teil dieses Repos.

## Was dieses Repo nicht ist

- **Kein Multi-Tenant-Dienst.** Ein Satz Prozesse, eine Datenbank, eine Person. Zwei Personen im
  Haushalt heißt zwei Installationen mit eigenen Tokens und eigener `KORE_INSTANCE_ID`.
- **Kein Weg zu gehosteten Assistenten.** Siehe oben.
- **Keine medizinische Software.** Weder Kore noch dein Coach stellen Diagnosen.

## Mitmachen

[CONTRIBUTING.md](CONTRIBUTING.md) (englisch) beschreibt Tests, Lockfiles und was nie in ein Issue
gehört. Bitte poste nirgends Gesundheitsdaten oder Tokens.

## Lizenz

MIT, siehe [LICENSE](LICENSE).
