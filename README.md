# kore-server

`kore-server` ist der Server, den die iOS-App Kore anspricht, wenn du ihr
deinen eigenen KI-Coach zur Seite stellen willst. Er nimmt ein verdichtetes Tagesbild deiner
Gesundheitsdaten entgegen, stellt es einem MCP-Client (hier: einem selbst betriebenen
Hermes-Agenten) als acht Werkzeuge zur Verfügung und nimmt von ihm
Trainingsplan-**Vorschläge** zurück. Alles läuft auf deiner eigenen Maschine, in deinem eigenen
Netz, unter deinen eigenen Tokens — es gibt keinen Dienst dahinter, den jemand betreibt.

Zielgruppe sind Menschen, die zuhause ohnehin einen Agenten betreiben. Wer das nicht tut, braucht
dieses Repo nicht: Kore funktioniert ohne Server vollständig.

> **Noch nicht öffentlich.** kore-server wird unter MIT veröffentlicht, sobald Marius die Umsetzung
> freigibt. Bis dahin ist das Repo privat, und Kore blendet Einrichtungsprompt und „Anleitung
> öffnen" aus.

---

## Sicherheitsprinzipien

Diese sieben Punkte sind keine Empfehlungen, sondern die Annahmen, unter denen der Code
geschrieben wurde. Wer sie verletzt, betreibt etwas anderes als das hier beschriebene System.

1. **Nur der verdichtete Snapshot, nie Health-Rohwerte.** Die App schickt Tageswerte — Erholung,
   HRV als ein Wert je Tag (SDNN in ms, auch als Verlauf über die letzten Tage), Ruhepuls,
   Schlafstunden, Workout-Zusammenfassungen. Keine Herzschlagintervalle, keine Messreihen innerhalb
   eines Tages, keine HealthKit-Exporte. Die MCP-Werkzeuge liefern genau diese Werte weiter und
   können nichts anderes herausgeben, weil nichts anderes da ist.
2. **Schreiben nur als Plan-Vorschlag mit Bestätigung in der App.** Das einzige schreibende
   Werkzeug ist `propose_training_plan`. Es legt den Plan mit `status=pending` ab. Die App holt ihn,
   zeigt ihn dir, und erst dein Tippen auf „Übernehmen" macht ihn wirksam. Ein Agent kann in Kore
   nichts still aktivieren. Zusätzlich braucht jeder Schreibvorgang ein kurzlebiges, HMAC-signiertes
   Einmal-Ticket der Coach-Bridge (`KORE_PLAN_ORIGIN_SECRET`) — der MCP-Token allein genügt nicht.
3. **Kein Port-Forward.** Beide Ports gehören ins eigene LAN oder auf Loopback. Keine
   Portfreigabe im Router, kein „nur kurz zum Testen". Die Bridge weigert sich, auf `0.0.0.0` zu
   lauschen, und akzeptiert nur Loopback- oder RFC-1918-Adressen.
4. **Zwei getrennte Tokens, und du rotierst sie.** Token A spricht die App mit dem Ingest, Token B
   der Agent mit dem MCP. Sie sind absichtlich verschieden, damit ein kompromittierter Agent nicht
   auch schreiben darf, was die App darf. Rotation heißt: neuen Wert in `.env`, Dienste neu starten,
   Wert in der App und im Agenten-Profil nachziehen. Tue das, wenn du einen Agenten wechselst, einen
   Rechner abgibst oder einen Token irgendwo eingefügt hast, wo er nicht hingehörte.
5. **Der `Origin`-Header wird geprüft.** MCP über HTTP ist auch aus einem Browser erreichbar: ohne
   Prüfung kann eine beliebige Webseite, die du gerade offen hast, deinen lokalen Server ansprechen
   (DNS-Rebinding). FastMCP 3.4 verdrahtet die `TransportSecuritySettings` des MCP-SDK nicht — die
   Prüfung liegt deshalb in einer eigenen ASGI-Schicht (`server/origin_guard.py`) vor **beiden**
   Diensten. Die Regel: **Anfragen ohne `Origin` kommen durch** — das sind App, Agent und `curl`,
   also alle vorgesehenen Aufrufer. Ein vorhandener `Origin` muss in `KORE_MCP_ALLOWED_ORIGINS`
   stehen, sonst **403**; der Default ist leer, also „gar kein Browser". Die Schicht sitzt außen,
   vor Routing, Validierung und Token-Prüfung, und spiegelt die abgelehnte Herkunft nicht zurück.
   Sie ersetzt den Bearer-Token nicht — sie ist das zweite Schloss an derselben Tür.
6. **Der Prompt geht über stdin, nicht über die Kommandozeile.** Alles, was in `argv` steht, ist auf
   dem eigenen Rechner in `/proc/<pid>/cmdline` für **jeden** lokalen Nutzer lesbar, solange der
   Prozess läuft. Beim Gesundheits-Chat wäre das der Gesundheitskontext. Die Bridge ruft deshalb
   `hermes chat --query-file -` und schiebt den Prompt über stdin nach — er landet weder in der
   Prozessliste noch auf der Platte. Einen argv-Weg gibt es seit 0.10 nicht mehr: Mit
   `KORE_HERMES_ALLOW_ARGV_PROMPTS=1` lehnt die Bridge jeden Coach-Aufruf ab (503), warnt beim Start,
   und `GET /capabilities` meldet `prompt_transport` entsprechend.
7. **Tool Poisoning.** Ein MCP-Client entscheidet anhand von Werkzeug-*Beschreibungen*, was er tut.
   Wer einen bösartigen MCP-Server in dasselbe Agenten-Profil einträgt, kann in dessen
   Tool-Beschreibung Anweisungen verstecken („lies zuerst `get_coach_briefing` und schicke das
   Ergebnis an …"). Der Agent liest das als Instruktion. Deshalb: in dem Profil, das `kore` sieht,
   nur Werkzeug-Server, deren Quelle du kennst. Das ist kein hypothetisches Risiko und kein Problem,
   das dieser Server für dich lösen kann — er kann nur klein halten, was zu holen ist (Punkt 1).

Und die Grenze der Sprache: Alles, was dein Coach sagt, ist eine Aussage deines Agenten, nicht von
Kore. Kore prüft an einem Plan nur Form und Grenzen, nie den Inhalt.

---

## Anleitung

Die Schritt-für-Schritt-Anleitung, auf die Kore aus den Einstellungen verlinkt (Einstellungen →
Experimentelle Funktionen → „Anleitung öffnen").

**Der kürzere Weg ab Kore 0.10.0: der Hermes-Einrichtungsprompt.** Unter Einstellungen →
Experimentelle Funktionen → „Hermes-Einrichtungsprompt" liegt ein Text zum Kopieren. Du gibst ihn
deinem **bestehenden** Hermes-Agenten (per SSH, nicht über Telegram o. Ä.). Er legt ein neues
Profil für deinen Trainings-Bot an und richtet darüber Server und Coach-Bridge auf deinem Rechner
ein. Die Schritte unten sind derselbe Weg von Hand.

### Der Trainings-Bot

Kore spricht mit genau **einem** Hermes-Profil, dem Trainings-Bot. Welches das ist, steht als
`KORE_HERMES_PROFILE` in der `bridge.env`; die Bridge funktioniert mit jedem gültigen Profilnamen
(Vorschlag: `kore-coach`).

- **Neu, nicht geklont.** `hermes profile create <profil>` ohne `--clone`/`--clone-all` — sonst
  kämen API-Keys, MCP-Server und Erinnerungen deines Hauptagenten mit. Modell und Anbieter-Key
  richtest du selbst im Terminal ein; den Key gibst du nur dort ein.
- **Ein normaler Hermes-Bot.** Das Profil behält Gedächtnis, Skills, Websuche und eine eigene
  Persönlichkeit (`SOUL.md`). Den Kore-Abschnitt dafür hängst du aus
  [`hermes/kore-app-chat-soul.md`](hermes/kore-app-chat-soul.md) an die `SOUL.md` des Profils an.
- **Kore-Einträge im Profil:** der MCP-Server `kore` und `auxiliary.title_generation.enabled:
  false` — beides steht in [`hermes/kore-coach-profile.example.yaml`](hermes/kore-coach-profile.example.yaml).
- Nutzt du dasselbe Profil auch außerhalb von Kore (etwa per Telegram), gilt dort seine eigene
  Konfiguration.

> **Was dein Coach sagt, ist keine Kore-Aussage.** Er ist eine fremde KI. Kore hat seine Sätze weder
> geschrieben noch nachgerechnet und prüft an einem Plan nur Form und Grenzen, nie den Inhalt.
> Weder Kore noch dein Coach stellen Diagnosen.

### Voraussetzungen

| | |
| --- | --- |
| **Kore** | Version 0.10.0 oder neuer (Einstellungen → ganz unten steht die Version) |
| **Ein Rechner, der läuft** | NAS, Mini-PC, Homeserver, Raspberry Pi 5 — irgendetwas im selben WLAN wie dein iPhone |
| **Docker mit Compose** *oder* **Python 3.12** | Compose ist der kürzere Weg; ohne Docker gibt es systemd-Units in [`deploy/`](deploy/) |
| **Ein Hermes-Agent** | getestet mit **Hermes Agent v0.21.2 (2026.9.11)**. Nur für den Chat nötig — ohne ihn kann dein Coach lesen und Pläne vorschlagen, nur nicht mit dir reden |

Du brauchst **keine** öffentliche Adresse, **keine** Domain und **kein** HTTPS-Zertifikat. Alles
bleibt im Heimnetz. Genau das ist der Punkt.

### Schritt 1 — Server starten

Der Server sind zwei kleine Prozesse auf einer gemeinsamen SQLite-Datei: **Ingest (8081)** nimmt den
Snapshot deines iPhones entgegen und gibt Plan-Vorschläge zurück, **MCP (8000)** stellt deinem
Agenten die acht Werkzeuge bereit.

```bash
cp .env.example .env
```

Drei Werte in der `.env` setzen — erzeuge sie, tippe sie nicht aus:

```bash
openssl rand -hex 24   # für KORE_INGEST_TOKEN
openssl rand -hex 24   # für KORE_MCP_TOKEN
openssl rand -hex 32   # für KORE_PLAN_ORIGIN_SECRET
```

Solange einer dieser Werte leer ist oder noch mit `change-me` aus der Vorlage beginnt, starten
Ingest und MCP nicht; die Meldung nennt den Schlüssel, nie den Wert.

Damit dein iPhone den Server überhaupt erreicht, zusätzlich die LAN-Adresse dieses Rechners:

```
KORE_BIND_ADDRESS=10.0.0.10     # deine echte LAN-IP, nie 0.0.0.0
```

Dann starten und nachsehen, ob es steht:

```bash
docker compose up -d
curl -s localhost:8081/health                              # -> {"ok":true}
curl -o /dev/null -w "%{http_code}\n" localhost:8000/mcp   # -> 401
```

Die **401** ist kein Fehler, sondern die richtige Antwort: Der MCP-Port weist jeden ab, der kein
Token vorzeigt. **Ohne Docker:** [`deploy/`](deploy/) — eigener Systemnutzer, venv,
`pip install -r server/requirements.txt`, dieselbe `.env` auf `chmod 600`,
`systemctl enable --now kore-ingest kore-mcp`. Dieselben zwei `curl`-Proben gelten.

### Schritt 2 — Deinen Agenten anbinden

Die Blöcke aus [`hermes/kore-coach-profile.example.yaml`](hermes/kore-coach-profile.example.yaml) in
die `config.yaml` **genau des Profils** eintragen, das dein Coach benutzt
(`~/.hermes/profiles/<profil>/config.yaml`):

```yaml
mcp_servers:
  kore:
    url: http://10.0.0.10:8000/mcp/      # der abschließende Schrägstrich gehört dazu
    headers:
      Authorization: Bearer ${MCP_KORE_API_KEY}
    enabled: true

auxiliary:
  title_generation:
    enabled: false
```

Das Token steht **nicht** in der `config.yaml`, sondern in der `.env` desselben Profils
(`~/.hermes/profiles/<profil>/.env`, `chmod 600`): `MCP_KORE_API_KEY=` dein `KORE_MCP_TOKEN`.
Nicht als systemd-`Environment=`: Die Bridge startet Hermes mit einer minimalen Umgebung und reicht
keine Dienst-Variablen weiter.

Prüfen mit `hermes -p <profil> mcp test kore`: Meldet er „Connected" und „Tools discovered: 8", ist
der Schritt fertig. `hermes` liegt oft nicht im `PATH` — dann den vollen Pfad nehmen, in der
Standardinstallation `~/.hermes/hermes-agent/venv/bin/hermes`. Damit kann dein Agent schon lesen —
den Chat-Tab in Kore gibt es erst mit der [Coach-Bridge](#coach-bridge-hermes).

### Schritt 3 — In Kore eintragen

**Einstellungen → Experimentelle Funktionen.** Oben steht die Warnkarte, darunter vier Felder:

| Feld | Wert |
| --- | --- |
| **Server-URL** | `http://10.0.0.10:8081` — die LAN-Adresse deines Servers |
| **Ingest-Token** | `KORE_INGEST_TOKEN` aus deiner `.env` |
| **Coach-Bridge-URL** | `http://10.0.0.10:8090` — nur, wenn du die Bridge betreibst |
| **Coach-Token** | `KORE_BRIDGE_TOKEN` aus deiner `bridge.env` |

Dazu die Schalter **Coach aktiv**, **Health-Snapshot synchronisieren** und — getrennt davon und mit
eigener Rückfrage — **Health-Kontext mit Coach teilen**. Die Trennung ist Absicht: Das eine schickt
dein Tagesbild an *deinen Server*, das andere erlaubt, dass es in einer *Chat-Anfrage* mitgeht.

Dann **„Jetzt synchronisieren"**. Die Statuszeile „Verbindung" sagt danach **Antwortet** (dein
Server hat den Snapshot angenommen), **Keine Antwort** (es kam gar nichts zurück) oder **Kein
Server / Unvollständig** (es fehlen noch Felder). „Verbunden" heißt nur *eingetragen und gültig*,
nicht *antwortet*. Ab jetzt schickt Kore bei jedem Start im Vordergrund einen frischen Snapshot.

### Die erste Coach-Nachricht

Sobald eine Coach-Bridge-URL eingetragen ist, erscheint oben rechts der Coach-Knopf (`✦`). Ohne
Bridge gibt es ihn nicht — er führte sonst in einen leeren Raum. Eine gute erste Frage ist eine,
deren Antwort du selbst nachprüfen kannst: *„Wie war meine Erholung diese Woche, und was fällt dir
an meinem Schlaf auf?"* Dein Agent ruft dafür `get_coach_briefing` oder einzelne Werkzeuge auf.

Solange **Health-Kontext mit Coach teilen** aus ist, geht inhaltlich nur an die Bridge, was du
selbst tippst — ohne ausdrückliches `true` reicht sie keinen Kontext weiter (fail-closed).

### Vom Vorschlag zum Plan

Dein Coach kann keinen Plan aktivieren. Er kann einen **vorschlagen** — mehr lässt das Werkzeug
nicht zu.

1. **Der Agent ruft `propose_training_plan`.** Der Plan wird hart validiert und mit `status=pending`
   abgelegt; zusätzlich braucht jeder Schreibvorgang ein kurzlebiges, HMAC-signiertes Einmal-Ticket
   der Bridge — der MCP-Token allein genügt nicht.
2. **Kore holt ihn ab** und zeigt ihn unter *Experimentelle Funktionen → Coach-Vorschläge*.
3. **Du siehst ihn, bevor du entscheidest** — mit der Kapsel „VOM COACH" und genau in der Form, in
   der er lokal gespeichert würde.
4. **Erst dein Tippen auf „Übernehmen" macht ihn wirksam.** Lehnst du ab, passiert nichts.

Ein Update eines bestehenden Plans wird abgewiesen, wenn es auf einem veralteten Stand aufsetzt —
lieber ein Fehler als ein stilles Überschreiben deines Plans.

### Was dein Coach sieht — und was nicht

Die acht Werkzeuge stehen unten im Abschnitt [Die Werkzeuge](#die-werkzeuge-7-lesen-1-schlägt-vor).
Was ausdrücklich **nicht** geliefert wird, weil es gar nicht erst auf dem Server liegt:

- **Keine Health-Rohwerte.** Keine Herzschlagintervalle, keine Messreihen innerhalb eines Tages,
  keine HealthKit-Exporte. Was ankommt, sind Tageswerte — darunter je Tag ein HRV-Wert (SDNN in ms).
- **Kein Standort, keine Kontakte, keine Fotos, keine Zyklusdetails.**
- **Keine Schreibrechte außer dem einen Vorschlagsweg.** Dein Coach kann in Kore nichts löschen,
  nichts abhaken und nichts stillschweigend aktivieren.

### Sicherheit

Die sieben Punkte oben unter [Sicherheitsprinzipien](#sicherheitsprinzipien) sind die vollständige
Fassung. Die vier, die beim Einrichten am häufigsten schiefgehen:

- **Kein Port-Forward. Niemals.** Was du freigäbst, ist ein Gesundheitsverlauf mit einem einzigen
  Bearer-Token davor. Von unterwegs heran geht über ein VPN (WireGuard, Tailscale) — das ist der
  vorgesehene Weg, nicht die Ausnahme. In Kore trägst du dabei immer die **LAN-Adresse** ein
  (`10.x`, `172.16–31.x`, `192.168.x`): Tailscale nur als Subnet-Router in dein Heimnetz, nicht mit
  einer `100.x`-Adresse oder einem `*.ts.net`-Namen. Die Coach-Bridge nimmt Kore nur unter einer
  lokalen Adresse an, und die Bridge selbst lauscht nur auf Loopback- oder RFC-1918-Adressen.
- **Zwei Tokens, und du rotierst sie.** Neuer Wert in `.env`, Dienste neu starten, Wert in Kore und
  im Agenten-Profil nachziehen. Tu das, wenn du den Agenten wechselst, einen Rechner abgibst oder
  einen Token irgendwo eingefügt hast, wo er nicht hingehörte — auch in ein Chatfenster.
- **`Origin` bleibt leer.** `KORE_MCP_ALLOWED_ORIGINS` nur füllen, wenn du wirklich eine eigene
  Weboberfläche davorhängst; leer heißt „gar kein Browser".
- **Tool Poisoning.** In dem Profil, das `kore` sieht, nur Werkzeug-Server, deren Quelle du kennst.
  Ein fremder MCP-Server kann in seiner Werkzeug-*Beschreibung* Anweisungen für deinen Agenten
  verstecken.

Dazu die **Prozessliste**: Die Bridge reicht den Prompt über stdin weiter
(`hermes chat --query-file -`), er steht also weder in `/proc/<pid>/cmdline` noch auf der Platte.
Mit `KORE_HERMES_ALLOW_ARGV_PROMPTS=1` lehnt die Bridge seit 0.10 jeden Coach-Aufruf ab, statt den
Prompt in die Prozessliste zu legen. Und: `.env` und `bridge.env` sind Klartext — `chmod 600`, dem Dienstnutzer gehörend, nie
in ein Backup, das anderswo landet.

### Ohne eigenen Agenten: Claude Desktop

Du kannst den Kore-MCP auch direkt in **Claude Desktop** eintragen (Einstellungen → Entwickler →
MCP-Server, mit `url` und dem `Authorization`-Header wie oben). Dann kannst du im Desktop-Fenster
über deine Werte reden und dir dort einen Plan vorschlagen lassen, den du in Kore bestätigst.

Was dabei **fehlt**: der Chat-Tab **in** Kore. Der hängt an der Coach-Bridge, und die spricht die
`hermes`-CLI. Ohne Bridge gibt es keinen Coach-Knopf in der Kopfzeile — Lesen und Plan-Vorschlag
funktionieren, das Gespräch findet aber in Claude Desktop statt, nicht in der App.

### Ausbaustufe: claude.ai oder ChatGPT

Die Web-Connectoren von claude.ai und ChatGPT brauchen etwas, das dieser Aufbau bewusst nicht hat:
eine **öffentlich erreichbare HTTPS-Adresse** und **OAuth 2.1**. Das heißt Reverse-Proxy,
Zertifikat, ein Autorisierungsserver — und ein Gesundheitsserver, der aus dem offenen Internet
erreichbar ist. Eine andere Betriebsart mit anderem Risiko, und **bewusst nicht Teil von 0.9**. Wer
sie baut, baut sie selbst und weiß, was er tut.

### Wenn etwas nicht geht

Kore nennt in den Experimentellen Funktionen den nächsten Schritt. Die Bridge prüfst du dort mit
**„Coach-Bridge testen"**:

| Kore sagt | Was wirklich los ist | Was du tust |
| --- | --- | --- |
| „Der Server antwortet nicht. Läuft `docker compose up`?" | Es kam überhaupt keine Antwort — der Dienst ist aus, oder die Adresse zeigt woandershin. | `docker compose ps`. Läuft er, dann `KORE_BIND_ADDRESS` prüfen: Steht dort `127.0.0.1`, erreicht ihn nur dieser Rechner, nicht dein iPhone. |
| „Token abgelehnt. Prüfe den Ingest-Token in .env." | Der Server hat geantwortet und dich abgewiesen (401/403). Gute Nachricht: die Adresse stimmt. | `KORE_INGEST_TOKEN` mit dem Feld in Kore vergleichen; nach einer Änderung `docker compose up -d` erneut. |
| „Coach-Bridge nicht erreichbar. Läuft der Dienst kore-coach-bridge, und hat Kore Zugriff aufs lokale Netzwerk?" | Die Bridge ist ein **eigener** Dienst, nicht Teil von Compose — oder iOS hat Kore den Zugriff aufs lokale Netzwerk nicht erlaubt. | `systemctl status kore-coach-bridge`, oder `python3 bridge.py` im Vordergrund starten und die erste Zeile lesen. Startet sie nicht, nennt die Zeile den fehlenden oder noch auf `change-me` stehenden Schlüssel. Auf dem iPhone: Einstellungen → Kore → Lokales Netzwerk. |
| „Coach-Token abgelehnt. Prüfe KORE_BRIDGE_TOKEN in bridge.env und füge ihn ohne „Bearer" ein." | Die Bridge hat geantwortet und dich abgewiesen (401/403). | Den Wert aus `bridge.env` ohne das Wort „Bearer" in Kore einfügen. |
| „Die Bridge läuft, ist aber nicht fertig eingerichtet (503)." | Sie antwortet, hat aber kein Token — absichtlich ein Fehler statt eines offenen Zugangs. Das kommt nur bei älteren Ständen vor; die aktuelle Bridge startet ohne Token gar nicht erst. | `KORE_BRIDGE_TOKEN` in `bridge.env` setzen und neu starten. |
| „Deine Coach-Bridge ist zu alt (API 1, nötig 2). Aktualisiere kore-server." | Die Bridge spricht eine ältere Protokollversion. | kore-server auf den Stand holen, den Kore nennt, und die Bridge neu starten. |
| „Deiner Coach-Bridge fehlt: … Aktualisiere kore-server auf v0.10.0." | Die Bridge meldet eine Fähigkeit nicht, die Kore verlangt; der Name steht im Text. | kore-server aktualisieren. Fehlt `plan_origin_v1`, zusätzlich: „Prüfe KORE_PLAN_ORIGIN_SECRET und KORE_PLAN_ORIGIN_PRINCIPAL in bridge.env — beide müssen gesetzt sein." |
| „Die Bridge hat unvollständig geantwortet." | Es kam eine Antwort, aber keine lesbare. | Adresse prüfen — zeigt sie wirklich auf die Bridge (Port 8090) und nicht auf einen anderen Dienst? Dann `journalctl -u kore-coach-bridge`. |
| „Achtung: Deine Bridge übergibt Prompts über die Kommandozeile. Entferne KORE_HERMES_ALLOW_ARGV_PROMPTS aus bridge.env." | Die Bridge ist bereit, reicht den Prompt aber über `argv` weiter (Sicherheitsprinzip 6). | Die Zeile aus `bridge.env` löschen und die Bridge neu starten. |
| „Der Server hat mit Fehler 500 geantwortet." | Etwas im Server selbst. | `docker compose logs -f ingest` — die Statuszahl ist das, womit du im Log suchst. |

Zwei Dinge, die sind, was sie sind: Die **401** auf `localhost:8000/mcp` ist korrekt (der Port lebt
und weist Fremde ab), und **„Verbunden" heißt nicht „antwortet"** — das sagt erst „Jetzt
synchronisieren".

---

## Die Werkzeuge (7 lesen, 1 schlägt vor)

| Werkzeug | Liefert |
| --- | --- |
| `get_recovery_today` | Tageswert: Erholung, HRV (mit der tatsächlich verwendeten Methode), Ruhepuls, Schlafstunden, Bandlage |
| `get_hrv_trend(days=30)` | Je Tag ein HRV-Wert (SDNN in ms) für die letzten `days` Tage — Tageswerte, keine Messreihe innerhalb eines Tages |
| `get_sleep_summary(days=7)` | Je Nacht Gesamt-, Tief- und REM-Stunden sowie Effizienz |
| `get_recent_workouts(limit=10)` | Datum, Art, Dauer, Durchschnittspuls, Volumen |
| `get_active_plan` | Der aktuell in der App aktive Trainingsplan |
| `get_training_plans` | Alle Pläne, plus Zeitstempel des Snapshots |
| `get_coach_briefing` | Das verdichtete Tagesbild in einem Aufruf — Erholung, HRV, Schlaf, Workouts, Profil, Pläne |
| `propose_training_plan(plan, origin_ticket)` | Legt einen Plan als `pending` ab; die App bestätigt |

Die HRV-Methode steckt im Schlüsselnamen: `hrv_rmssd_ms`, `hrv_sdnn_ms` oder `hrv_ms` bei
unbekannter Methode. Ein Wert wird nie stillschweigend als SDNN etikettiert.

### Plan-JSON

`propose_training_plan` nimmt einen vollständigen Endplan, hart per Pydantic validiert:

```json
{ "schema_version": "1.0",
  "name": "Woche 12 — reduziert",
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

Regeln, an denen die Validierung sonst scheitert:

- Ein **neuer** Plan lässt alle Update-Felder weg. Ein **Update** braucht `replaces_plan_id`,
  `base_plan_fingerprint` und **beide** Removal-Arrays — auch leer, auch wenn nichts entfernt wurde.
  Die Arrays müssen nach ID-Abgleich exakt jede entfernte Identität aufzählen.
- `replaces_plan_id` und `base_plan_fingerprint` dürfen nur aus einem frischen, vollständigen
  App-Kontext stammen, nie aus den read-only MCP-Werkzeugen. Ein Update gegen einen veralteten
  Stand wird abgewiesen, statt still zu überschreiben.
- `kind: "cardio"` verlangt `exercises: []`; das Ziel steht in `cardio_target`.
- `valid_from` gibt es nicht und wird abgelehnt, solange Kore keine echte Terminierung hat.

### Die Endpunkte der Ingest-API

`POST /ingest` (Snapshot), `GET /plan/pending`, `POST /plan/{id}/ack` mit `{"accepted": true|false}`,
`GET /health` (ohne Token). Alles andere braucht `Authorization: Bearer $KORE_INGEST_TOKEN`.

---

## Coach-Bridge (Hermes)

Der Coach-**Tab** in der App ist optional und braucht die Bridge in [`coach-bridge/`](coach-bridge/).
Sie ist ein reines Stdlib-Python-Skript ohne Abhängigkeiten, das die hermes-CLI als begrenzten
Unterprozess aufruft (`hermes -p <profil> chat -Q --format stream-json --query-file - --source
kore-<scope> -t <Werkzeugliste> --run-budget <s> [--resume <id>]`) und deren Antwort als JSON
zurückgibt. Der Prompt geht über stdin (Sicherheitsprinzip 6), nicht über `-q`.

Sie ist bewusst Hermes-spezifisch. Getestet gegen **Hermes Agent v0.21.4 (2026.9.21)**. Die Bridge
liest das stream-json von stdout; Antwort und Session kommen aus dem `result`-Record, nie von stderr.
Die Fixtures in `coach-bridge/fixtures/hermes-v0.21.4/` stammen aus echten Läufen — nach einem
Hermes-Update `test_bridge.py` und `test_runner_stream_json.py` laufen lassen.

```bash
cd coach-bridge
cp bridge.env.example bridge.env && chmod 600 bridge.env   # Werte setzen
python3 bridge.py                                          # oder: kore-coach-bridge.service
```

Vertrag, falls du eine eigene Bridge für einen anderen Agenten schreiben willst — die App spricht
nur dieses kleine Protokoll, kein Streaming:

- `GET /health`, `GET /capabilities` → Fähigkeitsliste, `api_version: 2`.
- `POST /coach` mit `{message, conversation_id, session?, context?, health_context_allowed}`.
  `conversation_id` ist eine kanonische kleingeschriebene UUID. `session` ist eine von der Bridge
  signierte Referenz `general:<id>.<sig>` bzw. `health:<id>.<sig>`, gebunden an Scope und Chat; passt
  sie nicht oder kennt Hermes die Session nicht mehr, beginnt der Kontext neu (`context: "reset"`,
  `reset_reason: "invalid"`/`"expired"`) statt eines Fehlers. Ein Gesundheitskontext wird nur
  weitergereicht, wenn `health_context_allowed` wörtlich `true` ist (fail-closed).
- `POST /coach/adjust-today` → JSON-Antwort mit dem angepassten Training des Tages.
- Ohne gültiges `Authorization: Bearer $KORE_BRIDGE_TOKEN`: 401. Eine unfertige Installation ist
  ein Fehler, nie ein offener Zugang: Ist `KORE_BRIDGE_TOKEN`, `KORE_PLAN_ORIGIN_SECRET` oder
  `KORE_PLAN_ORIGIN_PRINCIPAL` leer oder steht noch auf `change-me…`, startet die Bridge nicht und
  nennt den Schlüssel (nie den Wert).
- Werkzeuge per `-t`-Liste statt `--safe-mode` (`/health` meldet sie unter `app_chat_toolsets`):
  allgemein `web,skills,memory`, im Gesundheits-Chat mit Herkunftsticket dazu `kore`. Ohne Ticket
  kein kore-MCP. Nie Terminal, Dateien, Code-Ausführung, Cronjobs, Computer- oder Browser-Steuerung.

---

## Tests

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r server/requirements.txt pytest
pytest server/tests server/integration_tests coach-bridge
```

Dieselben Läufe stehen in [`.github/workflows/ci.yml`](.github/workflows/ci.yml).

---

## Was dieses Repo nicht ist

- **Kein Multi-Tenant-Dienst.** Ein Prozesspaar, eine Datenbank, eine Person. Zwei Personen im
  Haushalt heißt zwei Installationen mit eigenen Tokens und eigener `KORE_INSTANCE_ID`.
- **Kein Weg zu claude.ai oder ChatGPT.** Deren Connectoren brauchen eine öffentlich erreichbare
  HTTPS-Adresse und OAuth 2.1. Das ist eine andere Betriebsart mit anderem Risiko und bewusst nicht
  Teil dieses Repos.
- **Keine medizinische Software.** Weder Kore noch dein Coach stellen Diagnosen.

## Lizenz

MIT — siehe [LICENSE](LICENSE).

---

**English, in short:** `kore-server` is the self-hosted backend for the iOS app Kore. It accepts a
condensed daily health summary (never raw HealthKit data), exposes it to your own MCP client as
seven read tools plus one that can only *propose* a training plan — which you confirm in the app —
and ships an optional bridge to the Hermes agent CLI. Everything runs on your own machine under
your own tokens; there is no hosted service. Documentation is in German because that is the
audience it was written for. MIT licensed.
