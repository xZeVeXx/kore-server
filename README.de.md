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
[Kopplung](#kopplung-mit-dem-iphone) · [Unterwegs per VPN](#unterwegs-per-vpn) ·
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
| **Coach-Bridge** (`coach-bridge/`, reines Python, nur Standardbibliothek) | 8090 | Chat: Kore spricht mit der Bridge, die Bridge startet `hermes` für ein einziges, dafür vorgesehenes Profil und gibt die Antwort zurück. Erledigt auch die [Kopplung](#kopplung-mit-dem-iphone). Läuft als systemd-Dienst auf dem Host. | deinem Heimnetz oder VPN-Adresse (`KORE_BRIDGE_BIND_HOST`) |
| **Ingest-API** (`server/`, FastAPI, in Docker) | 8081 | Nimmt die Tageswerte vom iPhone entgegen und gibt offene Plan-Vorschläge zurück. | deinem Heimnetz oder VPN-Adresse (`KORE_BIND_ADDRESS`) |
| **MCP-Server** (`server/`, FastMCP, in Docker) | 8000 | Lässt dein Hermes-Profil die Kore-Daten **lesen** (7 Werkzeuge) und einen Trainingsplan **vorschlagen** (1 Werkzeug). | **nur diesem Rechner** (`127.0.0.1`) |

Kore spricht mit der Bridge und der Ingest-API. Mit dem MCP-Server spricht nur Hermes.

Zwei Helfer liegen im Repo: `scripts/kore-doctor` prüft die Installation und verwaltet die
Kopplung, `hermes/kore-app-chat-soul.md` ist der Abschnitt, den du in die `SOUL.md` deines Coaches
aufnimmst.

> **Was dein Coach sagt, ist keine Kore-Aussage.** Er ist eine fremde KI. Kore hat seine Sätze weder
> geschrieben noch nachgerechnet und prüft an einem Plan nur Form und Grenzen, nie den Inhalt.
> Weder Kore noch dein Coach stellen Diagnosen.

## Voraussetzungen

| | |
| --- | --- |
| **Kore** | Version 0.13 oder neuer für die Kopplung. Ältere Versionen (ab 0.10) funktionieren mit dem Einrichtungsweg per statischem Token (siehe [Kopplung](#kopplung-mit-dem-iphone)). |
| **Ein Linux-Rechner, der läuft** | Homeserver, Mini-PC, NAS, Raspberry Pi 5 … im selben Netz wie dein iPhone. Docker mit Compose v2 für Ingest und MCP, Python 3 für die Bridge. |
| **Hermes Agent 0.21.4 oder neuer** | Installiert auf **demselben Rechner**: Die Bridge startet `hermes` als Kindprozess, und der MCP-Server ist nur lokal erreichbar. Getestet mit v0.21.4 und v0.21.5. Ohne Hermes funktionieren Lesen und Plan-Vorschläge über MCP weiter, nur zum Chatten fehlt der Partner. |
| **Dein eigener Schlüssel eines Modell-Anbieters** | Hermes braucht ein Modell. Bring deinen eigenen Anbieter-Schlüssel (oder ein lokales Modell) mit und gib ihn selbst in deinem Terminal ein, nie in einem Chat. |
| **Eine feste Adresse für den Server** | Eine DHCP-Reservierung im Router oder eine VPN-Adresse (siehe [VPN](#unterwegs-per-vpn)). |

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
4. legt ein **neues** Hermes-Profil für deinen Trainings-Bot an (nie ein Klon deines Hauptprofils),
   bittet dich, das Modell selbst in deinem Terminal einzurichten, trägt den Kore-Abschnitt in dessen
   `SOUL.md` ein, registriert den MCP-Server und schaltet Auto-Titel ab;
5. richtet die Bridge als systemd-Dienst ein und lässt `kore-doctor` und drei Ende-zu-Ende-Proben
   laufen;
6. endet mit einem Kopplungs-Link (und einem QR-Code im Terminal) für dein iPhone.

Er fragt vor jedem `sudo`-Befehl, jeder Paketinstallation, jeder Firewall-Änderung, jedem Löschen und
jeder Profil-Änderung. Texte aus dem Repo, von Webseiten und aus Tool-Ausgaben gelten im Prompt als
Daten, nicht als Anweisungen. Die manuellen Schritte unten sind derselbe Weg von Hand.

### Weg B: von Hand einrichten

Führ die Schritte als der Linux-Nutzer aus, der Bridge und Hermes betreibt (nicht als root).

**1. Den Code auf einem Release-Tag holen.**

```bash
git clone https://github.com/xZeVeXx/kore-server.git ~/kore-server
cd ~/kore-server
git checkout --detach v0.13.0
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
MCP nicht; die Meldung nennt den Schlüssel, nie den Wert. `KORE_MCP_ALLOWED_ORIGINS` bleibt leer.
Compose startet außerdem nicht ohne `KORE_STATE_GID`.

**4. Ingest und MCP starten.**

```bash
docker compose up -d --build
curl -s 10.0.0.10:8081/health                                  # -> {"ok":true}
curl -s -o /dev/null -w '%{http_code}\n' 127.0.0.1:8000/mcp    # -> 401
```

Die **401** ist die richtige Antwort: Der MCP-Port weist jeden ab, der kein Token vorzeigt. Ohne
Docker siehe [`deploy/`](deploy/) (eigener Systemnutzer, venv, systemd-Units).

**5. Das Hermes-Profil für deinen Coach anlegen.** Kore spricht mit genau einem Profil.

```bash
HERMES=~/.hermes/hermes-agent/venv/bin/hermes     # oder wo `hermes` liegt
$HERMES profile create kore-coach                 # ohne --clone / --clone-all
$HERMES -p kore-coach model                       # Modell selbst einrichten, in deinem Terminal
$HERMES -p kore-coach config set auxiliary.title_generation.enabled false
```

Klon nie dein Hauptprofil: Das würde dessen API-Schlüssel, MCP-Server und Erinnerungen in den Coach
kopieren. Dann trägst du drei Dinge in das neue Profil ein.

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

*Prüfen:* `$HERMES -p kore-coach mcp test kore` muss „Connected“ und „Tools discovered: 8“ melden.

**6. Die Coach-Bridge einrichten.**

```bash
mkdir -p ~/kore-coach-bridge && chmod 700 ~/kore-coach-bridge
cp ~/kore-server/coach-bridge/bridge.env.example ~/kore-coach-bridge/bridge.env
chmod 600 ~/kore-coach-bridge/bridge.env
```

Bearbeite `bridge.env` (nur absolute Pfade; systemd expandiert weder `$HOME` noch `~`):

| Schlüssel | Wert |
| --- | --- |
| `KORE_BRIDGE_TOKEN` | ein neuer Zufallswert (`openssl rand -hex 24`) |
| `KORE_BRIDGE_BIND_HOST` | dieselbe Adresse wie `KORE_BIND_ADDRESS` (`10.0.0.10`), nie `0.0.0.0`. Mit `127.0.0.1` erreicht dein iPhone die Bridge nicht. |
| `KORE_BRIDGE_PORT` | `8090` |
| `KORE_HERMES_BIN` | absoluter Pfad zu `hermes` |
| `KORE_HERMES_HOME` | das Home-Verzeichnis dieses Nutzers |
| `KORE_HERMES_PROFILE` | `kore-coach` |
| `KORE_INSTANCE_ID`, `KORE_PLAN_ORIGIN_PRINCIPAL` | dieselben Werte wie in der `.env` des Servers; später nie ändern |
| `KORE_PLAN_ORIGIN_SECRET` | derselbe Wert wie in der `.env` des Servers |

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

[Install]
WantedBy=multi-user.target
```

Die Bridge **schreibt** den Kopplungszustand (`<HOME>/kore-server/state`), darum muss dieser Pfad in
`ReadWritePaths` stehen. Fehlt er, macht `ProtectSystem=strict` die Kopplung kaputt: Die Bridge
startet, protokolliert `Device pairing unavailable` und beantwortet jeden Kopplungsversuch mit 401.
Danach:

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now kore-coach-bridge
```

(Liegt dein Zustandsordner nicht unter `<HOME>/kore-server/state`, setze `KORE_STATE_DIR` in
`bridge.env` und `.env` auf denselben absoluten Pfad und nimm diesen Pfad in `ReadWritePaths`; systemd
expandiert dort keine Variablen.) Ist eine Firewall wie
`ufw` aktiv, gib nur Port 8090 für dein lokales Subnetz frei. Docker umgeht `ufw` für seine eigenen
Ports, und der MCP-Port ist ohnehin lokal.

**7. Alles prüfen.**

```bash
python3 ~/kore-server/scripts/kore-doctor
```

Der Exit-Code muss 0 sein. Siehe [kore-doctor](#installation-prüfen-kore-doctor). Danach
[koppelst du dein iPhone](#kopplung-mit-dem-iphone).

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

- Der Code hat 20 Zeichen (100 Bit), gilt **10 Minuten** und ist nach dem **ersten** Versuch
  verbraucht, ob er gelang oder nicht. Ein neuer Code ersetzt den offenen. Nach 5 Fehlversuchen pro
  Minute von einer Adresse antwortet der Server mit 429.
- Der Link enthält nie ein langlebiges Token. Ein Gerätetoken sind 256 Bit Zufall; der Server
  speichert nur dessen SHA-256-Hash, im Zustandsordner, und protokolliert weder Codes noch Tokens.
- Das Gerätetoken gilt für die Bridge und die Ingest-API, sonst nichts. Der MCP-Server akzeptiert nur
  sein eigenes Token, das Geräte nie zu sehen bekommen.
- Ein abgefangener Link nützt außerhalb deines Heimnetzes oder VPNs nichts (der Server ist dort nicht
  erreichbar), gilt nur einmal und verfällt nach 10 Minuten.

Nützliche Optionen: `--name "Mein iPhone"` benennt das Gerät, das als Nächstes koppelt;
`--bridge-url` und `--ingest-url` setzen die Adressen, falls sie sich nicht aus `bridge.env` und
`.env` ableiten lassen. Akzeptiert werden private (10/8, 172.16/12, 192.168/16) und VPN-Adressen
(100.64/10) in IPv4 sowie `https://<name>.ts.net` (Tailscale serve, siehe VPN-Abschnitt), nie Loopback,
andere Hostnamen oder öffentliche Adressen.

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

## Unterwegs per VPN

Zuhause erreicht Kore den Server direkt über dein Heimnetz. Unterwegs nimmst du ein **VPN in dein
Heimnetz**. Die Regel: **nie einen Port weiterleiten, nie an `0.0.0.0` binden, nie einen öffentlichen
Tunneldienst nutzen** (Reverse-Tunnel, Tailscale Funnel und Ähnliches). Alle drei Dienste sprechen
Klartext-HTTP; im VPN-Tunnel verschlüsselt der Tunnel diesen Verkehr, zuhause nicht.

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

### Variante 2: Tailscale, Schritt für Schritt

Tailscale gibt jedem deiner Geräte eine stabile Adresse in `100.64.0.0/10`, die Bridge und Kore als
VPN-Adresse akzeptieren.

1. **Tailscale auf dem Server installieren** nach der Anleitung von Tailscale für deine
   Distribution und anmelden (`sudo tailscale up`).
2. **Die Tailscale-App auf dem iPhone installieren** und mit demselben Konto anmelden.
3. **Einen unauffälligen Rechnernamen wählen** (in der Admin-Konsole oder mit
   `sudo tailscale set --hostname=<name>`). Schaltest du je HTTPS-Zertifikate ein, stehen
   Rechnername und Name deines Tailnets in öffentlichen Zertifikats-Logs (Certificate Transparency),
   die jeder durchsuchen kann. Also nicht `<dein-name>-gesundheitsserver`, sondern etwas Neutrales.
4. **Die Tailscale-Adresse des Servers ermitteln:** `tailscale ip -4`, zum Beispiel `100.64.0.10`.
5. **Daran binden.** In der `.env` des Servers: `KORE_BIND_ADDRESS=100.64.0.10`. In `bridge.env`:
   `KORE_BRIDGE_BIND_HOST=100.64.0.10`. Dann neu starten: `docker compose up -d` und
   `sudo systemctl restart kore-coach-bridge`.
6. **Reihenfolge beim Booten.** Die Adresse gibt es erst, wenn Tailscale läuft. Für die Bridge trägst
   du `After=tailscaled.service` in die Unit ein (`sudo systemctl edit kore-coach-bridge`); sie
   versucht es ohnehin alle fünf Sekunden neu. Sind die Docker-Dienste nach einem Neustart nicht da,
   führ `docker compose up -d` erneut aus.
7. **Koppeln.** `kore-doctor pair` liest die Adressen aus `.env` und `bridge.env`, der Link enthält
   also schon `100.64.0.10`. Schalt Tailscale auf dem Handy ein und öffne dann den Link.
8. **Halte dein Tailnet persönlich** und seine Zugriffsregeln eng: Alles, was diese Adresse
   erreicht, kann deine Tokens ausprobieren.

Mit eingeschalteter Tailscale-App gilt dieselbe Adresse zuhause und unterwegs.

**Variante 2b: HTTPS mit `tailscale serve` (für Fortgeschrittene).** Lass Ingest und Bridge auf
`127.0.0.1` und lass Tailscale sie innerhalb deines Tailnets mit Zertifikat veröffentlichen, zum
Beispiel:

```bash
sudo tailscale serve --bg --https=443  http://127.0.0.1:8081    # Ingest
sudo tailscale serve --bg --https=8443 http://127.0.0.1:8090    # Bridge
```

Die Flags deiner Version zeigt `tailscale serve --help`; HTTPS-Zertifikate müssen in der
Admin-Konsole aktiviert sein. Kore verbindet sich dann mit `https://<rechner>.<tailnet>.ts.net` und
`https://<rechner>.<tailnet>.ts.net:8443`. `kore-doctor pair` akzeptiert diese Namen ebenfalls
(`--bridge-url https://<rechner>.<tailnet>.ts.net:8443 --ingest-url https://<rechner>.<tailnet>.ts.net`),
aber nur mit `https://`; jeden anderen Hostnamen lehnt es ab. Der Hinweis zu den Zertifikats-Logs
oben gilt auch hier.

## Sicherheit

### Prinzipien

Diese Punkte sind keine Empfehlungen, sondern die Annahmen, unter denen der Code geschrieben wurde.
Wer sie verletzt, betreibt etwas anderes als das hier beschriebene System.

1. **Nur der verdichtete Snapshot, nie Health-Rohwerte.** Die App schickt Tageswerte: Erholung,
   HRV als ein Wert je Tag (SDNN in ms, auch als Verlauf über die letzten Tage), Ruhepuls,
   Schlafstunden, Workout-Zusammenfassungen. Keine Herzschlagintervalle, keine Messreihen innerhalb
   eines Tages, keine HealthKit-Exporte. Die MCP-Werkzeuge liefern genau diese Werte weiter und
   können nichts anderes herausgeben, weil nichts anderes da ist.
2. **Schreiben nur als Plan-Vorschlag mit Bestätigung in der App.** Das einzige schreibende Werkzeug
   ist `propose_training_plan`. Es legt den Plan mit `status=pending` ab. Die App holt ihn, zeigt ihn
   dir, und erst dein Tippen auf „Übernehmen“ macht ihn wirksam. Ein Agent kann in Kore nichts still
   aktivieren. Zusätzlich braucht jeder Schreibvorgang ein kurzlebiges, HMAC-signiertes Einmal-Ticket
   der Coach-Bridge (`KORE_PLAN_ORIGIN_SECRET`); der MCP-Token allein genügt nicht.
3. **Kein Port-Forward.** Alle Ports gehören ins Heimnetz, auf eine VPN-Adresse oder auf Loopback.
   Die Bridge weigert sich, auf `0.0.0.0` zu lauschen, und akzeptiert nur eine konkrete
   IPv4-Loopback-, RFC-1918- oder VPN-Adresse (100.64.0.0/10). Der MCP-Port ist nur auf `127.0.0.1`
   veröffentlicht und aus deinem Netz gar nicht erreichbar.
4. **Getrennte Tokens, und du rotierst sie.** Ingest, Bridge und MCP haben je ein eigenes statisches
   Token, damit ein kompromittierter Agent nicht schreiben darf, was die App darf. Siehe
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
   (503) und warnt beim Start.
7. **Tool Poisoning.** Ein MCP-Client entscheidet anhand von Werkzeug-*Beschreibungen*, was er tut.
   Wer einen bösartigen MCP-Server in dasselbe Profil einträgt, das `kore` sieht, kann in dessen
   Beschreibungen Anweisungen verstecken. In dem Profil, das `kore` sieht, nur Werkzeug-Server, deren
   Quelle du kennst. Das kann dieser Server nicht für dich lösen; er kann nur klein halten, was zu
   holen ist (Punkt 1).
8. **Die Kopplung ist einmalig, kurz und widerrufbar.** Codes sind zufällig, gelten 10 Minuten, sind
   nach dem ersten Versuch verbraucht und gebremst. Gerätetokens sind zufällig, werden nur als Hash
   gespeichert und lassen sich jederzeit widerrufen. Weder Codes noch Tokens werden protokolliert.

### Netz

- **Kein Port-Forward. Niemals.** Was du freigäbst, ist ein Gesundheitsverlauf mit einem einzigen
  Bearer-Token davor. Von unterwegs geht es über ein [VPN](#unterwegs-per-vpn).
- **Klartext-HTTP.** Alle drei Dienste sprechen HTTP ohne TLS. Wer in deinem Netz mitlesen kann, sieht
  Tokens und Inhalte. Nutz es nur in einem Netz, dem du vertraust; Gäste und fremde Geräte gehören in
  ein eigenes WLAN. Ein VPN-Tunnel verschlüsselt den Weg durchs Internet.
- **`KORE_MCP_ALLOWED_ORIGINS` bleibt leer**, außer du hängst wirklich eine eigene Weboberfläche davor.
- **Geheimnisse liegen im Klartext.** `.env`, `bridge.env` und die `.env` des Profils: `chmod 600`,
  dem Dienstnutzer gehörend, nie in ein Backup, das anderswo landet, nie in ein Git-Repo, einen
  Screenshot oder ein Chatfenster. Die `.gitignore` hält `*.env`, Datenbanken, `*.bak*` und
  Schlüsseldateien aus Git, die `.dockerignore` aus dem Image.
- **Lieferkette.** Abhängigkeiten werden nur aus Lockfiles mit Hashes installiert
  (`--require-hashes`), das Basis-Image ist per Digest festgelegt, GitHub-Actions stehen auf
  Commit-SHAs, und ein gitleaks-Job sucht im gesamten Verlauf nach Geheimnissen.

### Geheimnisse tauschen

| Geheimnis | Wo | Wer es sonst noch braucht |
| --- | --- | --- |
| `KORE_INGEST_TOKEN` | `.env` des Servers | Kore, nur auf dem Experten-Weg |
| `KORE_MCP_TOKEN` | `.env` des Servers | `.env` des Profils als `MCP_KORE_API_KEY` |
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
| im allgemeinen Chat `web,skills,memory` erlauben, im Health-Chat mit Herkunftsticket zusätzlich `kore` | Dienst-Geheimnisse an Hermes vererben: Das Kind bekommt nur `HOME`, `PATH` und sieben Schlüssel wie `LANG`/`TZ` |
| Sessions der Quellen `kore-general`/`kore-health` lesen, um Titel und Vorgänger zu finden, und sie auf Wunsch oder nach 30 Tagen löschen | Sessions anderer Quellen anfassen oder das Gedächtnis oder die Konfiguration des Profils ändern |
| bei einem fehlgeschlagenen Turn eine Zeile `kore-turn-failed` (Klasse, Code, Exit) ins Journal schreiben | Prompt, Antwort, Token, Health-Werte oder Hermes-stderr ins Journal oder an die App geben |
| höchstens 2 Turns gleichzeitig ausführen und 64 Antworten bis zu 24 Stunden im Arbeitsspeicher halten | ohne Token, Origin-Geheimnis und Principal starten, auf einer öffentlichen Adresse lauschen oder per `sudo`/setuid mehr Rechte bekommen (`NoNewPrivileges=yes`) |

Feste Grenzen: Anfrage höchstens 256 KiB, Prompt 120 KiB, Hermes-Ausgabe 512 KiB, Laufzeit 150 s, dazu
30 s Socket-Timeout gegen hängende Verbindungen; die Ingest-API nimmt höchstens 16 MiB an. Tokens
werden zeitkonstant verglichen.

### Was bleibt (Restrisiken)

Kore schickt Health-Kontext nur in Chats mit Health-Freigabe, und die Bridge gibt das kore-MCP nur
dort frei. Was dein Bot danach damit tut, steuert Kore nicht:

- **Dein Modell-Anbieter sieht alles, was im Prompt steht**, im Health-Chat also den
  Health-Kontext. Welcher Anbieter das ist, bestimmst du im Profil.
- **Gedächtnis.** Der Bot kann sich mit `memory` etwas aus einem Health-Chat notieren. Solche Notizen
  fließen in jede spätere Session des Profils ein, auch in allgemeine App-Chats und in andere Kanäle
  desselben Profils (etwa Telegram). Löschen und Verfall in Kore erreichen sie nicht; dieses
  Gedächtnis verwaltest du in Hermes.
- **Externer Gedächtnis-Anbieter.** Nutzt das Profil einen, bringt `memory` dessen Werkzeuge mit, und
  Turns samt Health-Kontext können dorthin gehen. Prüfen mit `hermes -p <profil> memory status`.
- **Skills.** `skill_manage` gehört zu `skills`. Der Bot kann damit Skills des Profils anlegen oder
  ändern, auch wenn ihn ein Text aus der Websuche dazu verleitet. Ein solcher Skill wirkt in allen
  Kanälen des Profils weiter. Sieh dir neue oder geänderte Skills an, wenn dir etwas seltsam vorkommt.
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

## Daten und Datenschutz

Deine Daten bleiben auf **deinem** Server. Kore schickt deine Gesundheitsdaten nur an den Server, den
du koppelst, und dieses Repo enthält weder Analyse noch Telemetrie noch eigene ausgehende Aufrufe. Was
jeder Teil speichert:

| Wo | Was |
| --- | --- |
| **Ingest + MCP**, Docker-Volume `kore-db` (SQLite) | der jüngste Snapshot aus der App: Profil-Basisdaten, Erholung, HRV-Tageswerte bis 30 Tage, Ruhepuls, Schlaf, Workouts mit Puls und kcal, Trainingspläne mit Namen und Notizen; offene Plan-Vorschläge |
| **Kopplungszustand**, `state/` neben der `docker-compose.yml` | die Server-ID, der Hash eines offenen Einmalcodes und je gekoppeltem Gerät eine ID, ein Name, der Hash seines Tokens und zwei Zeitstempel. Keine Tokens oder Codes im Klartext |
| **Coach-Bridge** | keine Datenbank. Laufende Turns liegen im Arbeitsspeicher (bis 24 Stunden, 64 Antworten) und gehen beim Neustart verloren. Das Journal bekommt nur eine Fehlerzeile ohne Inhalt |
| **Hermes-Profil**, `~/.hermes/profiles/<profil>/` | die Chat-Sessions von Kore (Quellen `kore-general`/`kore-health`) und das Gedächtnis des Profils. Die Bridge löscht Kore-Sessions nach 30 Tagen ohne Aktivität und wenn du einen Chat in Kore löschst; das Gedächtnis fasst sie nie an |
| **Dein Modell-Anbieter** | die Prompts, und bei eingeschaltetem „Health-Kontext mit Coach teilen“ der Health-Kontext in jeder Nachricht. So funktioniert ein Chat; wähl deinen Anbieter entsprechend |

Ohne den Schalter „Health-Kontext teilen“ bekommt der Coach nur, was du tippst.

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
| „Deiner Coach-Bridge fehlt: …“ | Die Bridge meldet eine Fähigkeit nicht, die Kore verlangt; der Name steht im Text. | kore-server aktualisieren. Fehlt `plan_origin_v1`, zusätzlich `KORE_PLAN_ORIGIN_SECRET` und `KORE_PLAN_ORIGIN_PRINCIPAL` in `bridge.env` prüfen; beide müssen gesetzt sein. Fehlt `device_pairing_v1`, ist der Zustandsordner unbrauchbar (siehe die beiden Kopplungs-Zeilen unten). |
| Kopplung scheitert (401 `pairing_failed`) | Der Code ist falsch, abgelaufen (10 Minuten), durch einen früheren Versuch verbraucht, oder die Kopplung ist auf dem Server nicht verfügbar. | `kore-doctor pair` erneut ausführen und einen frischen Link nehmen. Klappt es weiter nicht: `journalctl -u kore-coach-bridge` und nach `Device pairing unavailable` suchen. |
| Kopplung: 429 `rate_limited` | Fünf Fehlversuche innerhalb einer Minute von einer Adresse. | Eine Minute warten und einen neuen Code nehmen. |
| Journal: `Device pairing unavailable: …` | Der Zustandsordner fehlt, oder die Bridge kann nicht hineinschreiben. | `kore-doctor init-state`, dann die Besitzer prüfen. Mit der gehärteten Unit muss `<HOME>/kore-server/state` in `ReadWritePaths` stehen. |
| `docker compose up` bricht mit „set KORE_STATE_GID …“ ab | In der `.env` fehlt `KORE_STATE_GID`. | `kore-doctor init-state` ausführen und die ausgegebene Zeile in die `.env` übernehmen. |
| Gekoppelt, aber die Ingest-API antwortet 401 | Der Ingest kann `devices.json` nicht lesen: Die Gruppe passt nicht. | `kore-doctor` hat einen Check „Zustandsordner“; `KORE_STATE_GID` auf die Gruppe des Ordners setzen und `docker compose up -d --force-recreate` ausführen. |
| `kore-doctor pair` lehnt eine Adresse ab | Akzeptiert werden private oder VPN-Adressen in IPv4 und `https://<name>.ts.net`, nicht `127.0.0.1`, andere Hostnamen, `http` auf einem Namen oder öffentliche Adressen. | `KORE_BRIDGE_BIND_HOST` und `KORE_BIND_ADDRESS` auf die Adresse setzen, die dein iPhone benutzt, oder `--bridge-url` und `--ingest-url` übergeben. |
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

Das Skript braucht nur die Standardbibliothek und gibt nie einen Geheimwert aus. Es prüft, dass

- der Ingest-`/health` 200 liefert (und mit fremdem Origin 403);
- der MCP-Server auf `127.0.0.1` ohne Token 401 liefert und über die Netzadresse nicht erreichbar ist;
- die Bridge ohne Token 401 liefert und ihr `/health` mit Token jede Fähigkeit aus
  [`contract/app-required-capabilities.json`](contract/app-required-capabilities.json),
  `prompt_transport: stdin` und `app_chat_toolsets` ohne Terminal-, Datei-, Code-, Cron-, Computer-
  und Browser-Werkzeuge meldet;
- `KORE_PLAN_ORIGIN_SECRET` (per Hash), `KORE_INSTANCE_ID` und `KORE_PLAN_ORIGIN_PRINCIPAL` in Server
  und Bridge gleich sind;
- das Profil existiert, `hermes -p <profil> mcp test kore` gelingt, Hermes mit genau dem Env läuft,
  das ihm die Bridge gibt, und die `SOUL.md` des Profils die aktuelle
  [`kore-app-chat-soul.md`](hermes/kore-app-chat-soul.md) enthält;
- der Zustandsordner der Kopplung mit den richtigen Rechten existiert und `KORE_STATE_GID` seine Gruppe
  ist.

Exit 0 nur ohne jede Abweichung. `--bridge-only` lässt Ingest, MCP und Server-`.env` weg, etwa wenn sie
auf einem anderen Rechner laufen. Die weiteren Unterbefehle sind `init-state`, `pair`, `devices` und
`revoke` (siehe [Kopplung](#kopplung-mit-dem-iphone)). Die meisten Meldungen sind deutsch; der
Kopplungs-Hinweis ist zweisprachig.

## Aktualisieren

```bash
cd ~/kore-server
git fetch --tags origin && git checkout --detach <neuer Tag>
docker compose up -d --build
sudo systemctl restart kore-coach-bridge
python3 scripts/kore-doctor
```

Lass `kore-doctor` (und `hermes -p <profil> mcp test kore`) auch nach jedem `hermes update` laufen.
Die Änderungen je Version stehen im [CHANGELOG.md](CHANGELOG.md).

**Von 0.10 oder früher:** `python3 scripts/kore-doctor init-state` ausführen und die ausgegebene Zeile
`KORE_STATE_GID=…` in die `.env` übernehmen (Compose startet sonst nicht); bei der gehärteten Unit den
Zustandsordner in `ReadWritePaths` ergänzen; den Abschnitt `## Kore-App-Chat` in der `SOUL.md` des
Profils durch die aktuelle Datei ersetzen (`kore-doctor` meldet einen veralteten). Der MCP-Server ist
jetzt nur noch auf `127.0.0.1` veröffentlicht.

## Referenz

### Die Werkzeuge (7 lesen, 1 schlägt vor)

| Werkzeug | Liefert |
| --- | --- |
| `get_recovery_today` | Tageswert: Erholung, HRV (mit der tatsächlich verwendeten Methode), Ruhepuls, Schlafstunden, Bandlage |
| `get_hrv_trend(days=30)` | Je Tag ein HRV-Wert (SDNN in ms) für die letzten `days` Tage: Tageswerte, keine Messreihe innerhalb eines Tages |
| `get_sleep_summary(days=7)` | Je Nacht Gesamt-, Tief- und REM-Stunden sowie Effizienz |
| `get_recent_workouts(limit=10)` | Datum, Art, Dauer, Durchschnittspuls, Volumen |
| `get_active_plan` | Der aktuell in der App aktive Trainingsplan |
| `get_training_plans` | Alle Pläne, plus Zeitstempel des Snapshots |
| `get_coach_briefing` | Das verdichtete Tagesbild in einem Aufruf: Erholung, HRV, Schlaf, Workouts, Profil, Pläne |
| `propose_training_plan(plan, origin_ticket)` | Legt einen Plan als `pending` ab; die App bestätigt |

Die HRV-Methode steckt im Schlüsselnamen: `hrv_rmssd_ms`, `hrv_sdnn_ms` oder `hrv_ms` bei unbekannter
Methode. Ein Wert wird nie stillschweigend als SDNN etikettiert. Ausdrücklich **nicht** geliefert wird,
was gar nicht erst auf dem Server liegt: Health-Rohwerte, Standort, Kontakte, Fotos, Zyklusdetails. Dein
Coach kann nichts löschen, nichts abhaken und nichts stillschweigend aktivieren.

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
`GET /health` (ohne Token). Alles andere braucht `Authorization: Bearer <token>`; das Token ist
`KORE_INGEST_TOKEN` oder das Token eines gekoppelten Geräts.

### Protokoll der Coach-Bridge

Die Bridge ist reines Python ohne Abhängigkeiten und ruft die `hermes`-CLI als begrenzten
Unterprozess auf (`hermes -p <profil> chat -Q --format stream-json --query-file - --source
kore-<scope> -t <Werkzeugliste> --run-budget <s> [--resume <id>]`). Sie ist bewusst
Hermes-spezifisch, getestet gegen Hermes Agent v0.21.4 und v0.21.5. Sie liest das stream-json von
stdout; Antwort und Session kommen aus dem `result`-Record, nie von stderr. Die Fixtures in
`coach-bridge/fixtures/hermes-v0.21.4/` stammen aus echten Läufen; nach einem Hermes-Update
`test_bridge.py` und `test_runner_stream_json.py` laufen lassen.

Falls du eine eigene Bridge für einen anderen Agenten schreibst, ist dies das kleine Protokoll, das die
App spricht:

- `GET /health`, `GET /capabilities`: Fähigkeitsliste, `api_version: 2`.
- `POST /pair` (ohne Bearer) und `POST /unpair` (Gerätetoken): siehe
  [`contract/pairing-v1.md`](contract/pairing-v1.md).
- `POST /coach` mit `{message, conversation_id, session?, context?, health_context_allowed}`.
  `conversation_id` ist eine kanonische kleingeschriebene UUID. `session` ist eine von der Bridge
  signierte Referenz `general:<id>.<sig>` bzw. `health:<id>.<sig>`, gebunden an Scope und Chat; passt
  sie nicht oder kennt Hermes die Session nicht mehr, beginnt der Kontext neu (`context: "reset"`)
  statt eines Fehlers. Ein Gesundheitskontext wird nur weitergereicht, wenn `health_context_allowed`
  wörtlich `true` ist (fail-closed).
- Mit zusätzlichem `client_message_id` (kanonische UUID) startet `POST /coach` einen Turn-Auftrag und
  antwortet sofort `202 {turn_id, status}`; dieselbe `client_message_id` im selben Chat liefert
  denselben Turn. Läuft im Chat schon ein Turn: `409`; mehr als zwei gleichzeitig: `429`.
- `GET /coach/turns/{turn_id}/events`: Server-Sent Events mit `id:`; `Last-Event-ID` spielt spätere
  Ereignisse erneut ab, alle 15 s `: keepalive`. Nur Text-Deltas `{"delta": …}`, am Ende
  `{"done": true, "used_kore": …, "context": …, "session"?: …, "title"?: …}` oder
  `{"error": …, "code": …}`; Werkzeugaufrufe bleiben in der Bridge.
- `GET /coach/turns/{turn_id}` liefert Stand bzw. Ergebnis (ein fertiger Turn wird mit dieser Antwort
  gelöscht); `POST /coach/turns/{turn_id}/cancel` beendet Hermes, idempotent. Der Puffer liegt nur im
  Arbeitsspeicher, höchstens 24 Stunden.
- `POST /coach/adjust-today`: JSON-Antwort mit dem angepassten Training des Tages.
- `POST /coach/forget` mit `{conversation_id, sessions: [<signierte Referenz>, …]}` oder
  `{scope: "health" | "all"}`: stoppt laufende Turns, löscht ihre Puffer und dann die Hermes-Sessions
  samt Kompressions-Vorgängern. Antwort `{deleted, not_found, failed}`; bestätigt ist das Löschen nur
  bei `failed: 0`.
- Verfall: Kore-Sessions ohne Aktivität seit `KORE_BRIDGE_SESSION_RETENTION_DAYS` Tagen (Standard und
  Obergrenze 30) löscht die Bridge beim Start und danach täglich, nie während ein Turn läuft.
- Ohne gültiges `Authorization: Bearer` (statisches Bridge-Token oder Gerätetoken): 401. Eine
  unfertige Installation ist ein Fehler, nie ein offener Zugang: Ist `KORE_BRIDGE_TOKEN`,
  `KORE_PLAN_ORIGIN_SECRET` oder `KORE_PLAN_ORIGIN_PRINCIPAL` leer oder steht noch auf `change-me…`,
  startet die Bridge nicht und nennt den Schlüssel (nie den Wert).
- Werkzeuge per `-t`-Liste statt `--safe-mode` (`/health` meldet sie unter `app_chat_toolsets`):
  allgemein `web,skills,memory`, im Health-Chat mit Herkunftsticket dazu `kore`. Ohne Ticket kein
  kore-MCP. Nie Terminal, Dateien, Code-Ausführung, Cronjobs, Computer- oder Browser-Steuerung.

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
