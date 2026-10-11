# Ohne Docker: systemd

Die beiden Units stammen aus der Referenzinstallation (Debian-12-Container).
Sie erwarten den Code unter `/opt/kore-server`, ein venv unter `/opt/kore-server/.venv`
und eine `/opt/kore-server/.env` nach dem Muster der `.env.example` im Repo-Wurzelverzeichnis.

```bash
sudo useradd --system --home /opt/kore-server kore
sudo mkdir -p /opt/kore-server && sudo chown kore /opt/kore-server
# server/ aus diesem Repo nach /opt/kore-server kopieren, dann:
sudo -u kore python3 -m venv /opt/kore-server/.venv
sudo -u kore /opt/kore-server/.venv/bin/pip install --require-hashes -r /opt/kore-server/requirements.lock

sudo install -m 600 -o kore .env /opt/kore-server/.env   # aus .env.example erzeugt
sudo cp kore-ingest.service kore-mcp.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now kore-ingest kore-mcp
```

`KORE_DB_PATH` in der `.env` auf einen absoluten Pfad setzen, den der Nutzer `kore`
schreiben darf, z. B. `/opt/kore-server/kore.db`. Liegt die Datenbank nicht unter
`/opt/kore-server`, trage ihren Ordner in `ReadWritePaths` beider Units ein (SQLite legt dort auch
`-wal` und `-shm` an); sonst ist das Dateisystem für die Dienste schreibgeschützt.

Keine Unit bindet auf `0.0.0.0`. Der Ingest lauscht auf `KORE_INGEST_HOST`, sonst auf
`KORE_BIND_ADDRESS` aus der `.env`, Standard `127.0.0.1`; damit dein iPhone ihn erreicht,
trägst du dort die LAN-Adresse dieses Rechners ein (z. B. `KORE_BIND_ADDRESS=10.0.0.10`).
Der MCP-Server lauscht auf `KORE_MCP_HOST`, Standard `127.0.0.1` — richtig, wenn dein
Hermes-Agent auf demselben Rechner läuft. Ein leerer Wert gilt als `127.0.0.1`. Beide Dienste
prüfen die Adresse vor dem Start und verweigern ihn bei `0.0.0.0`, `::`, Hostnamen und
öffentlichen Adressen; erlaubt sind nur konkrete IPv4-Adressen aus Loopback, RFC 1918 und
`100.64.0.0/10`. Die Ports nie im Router weiterleiten.

Dafür müssen die Units die Dienste über ihre Startskripte aufrufen: `python ingest_api.py`
und `python mcp_server.py` (so stehen sie in den Units hier). Eine selbst geschriebene Unit,
die `uvicorn --host …` direkt startet, überspringt die Adressprüfung.

Nur **im Docker-Container** lauschen beide Dienste auf `0.0.0.0` (`Dockerfile`,
`docker-compose.yml`); nach außen veröffentlicht Compose den Ingest ausschließlich auf
`KORE_BIND_ADDRESS` und den MCP-Server ausschließlich auf `127.0.0.1`. Die veröffentlichte
Adresse reicht Compose als `KORE_PUBLISHED_BIND` in den Container; dort prüft der Prozess sie
nach derselben Regel und startet sonst nicht. `KORE_PUBLISHED_BIND` steht nur in der
`docker-compose.yml`, **nicht** in die `.env` und nicht in die Units: Ein Dienst, der die Variable
sieht, hielte sich für einen Container und dürfte auf `0.0.0.0` lauschen. Vor Docker 28
waren auf `127.0.0.1` veröffentlichte Ports für Rechner im selben Layer-2-Segment erreichbar
(moby #45610): Docker 28 oder neuer verwenden, oder den Port per nftables auf Loopback
beschränken.

Die Units warten auf `network-online.target` und geben nie auf
(`StartLimitIntervalSec=0`, `RestartSec=5`): Fehlt beim Booten die LAN-Adresse noch
(„Cannot assign requested address", Errno 99), startet systemd den Dienst alle fünf
Sekunden neu, bis sie da ist.

Die `.env` gehört auf `chmod 600` und dem Dienstnutzer. Sie enthält die beiden
Tokens und das Origin-Geheimnis im Klartext. Solange einer dieser Werte leer ist oder
noch mit `change-me` aus der `.env.example` beginnt, starten Ingest und MCP nicht;
dasselbe gilt für ein Token mit weniger als 32 Zeichen (`openssl rand -hex 24` ergibt 48).
Die Meldung im Journal nennt den Schlüssel, nie den Wert.

Beide Units lesen dieselbe `.env` unter demselben Nutzer `kore`. Anders als unter Docker
Compose, wo jeder Container nur seine eigenen Schlüssel bekommt, sieht hier jeder Dienst
alle Schlüssel. Eine Trennung ist ohne zweiten Nutzer und zweite Datei wirkungslos, denn der
Nutzer kann `/proc/<pid>/environ` seiner eigenen Prozesse lesen; sie ist nicht vorgesehen.

Alle drei Units setzen `NoNewPrivileges=yes`: Weder die Dienste noch ein von der Bridge
gestartetes Hermes können per `sudo` oder setuid mehr Rechte bekommen als ihr Dienstnutzer.
Die Units für Ingest und MCP laufen außerdem in einer Sandbox wie die Bridge-Unit
(`ProtectSystem=strict`, `PrivateTmp=yes`, `UMask=0077`, Schutz von Kernel-Parametern,
-Modulen und Control Groups, `RestrictAddressFamilies`, `RestrictNamespaces`,
`RestrictSUIDSGID`, `LockPersonality`, leere `CapabilityBoundingSet=`) und mit Grenzen
`MemoryMax` (Ingest 768M, MCP 384M), `TasksMax=128` und `LimitNOFILE=4096`. Zeigt das Journal
`fork failed`, `Too many open files` oder einen OOM-Kill, erhöhe den jeweiligen Wert (ein
16-MiB-Snapshot braucht beim Parsen rund 400 bis 500 MiB).

Was du anpassen musst:

- **`ReadWritePaths=/opt/kore-server`** ist der Ordner der SQLite-Datei. Liegt `KORE_DB_PATH`
  woanders, trage dessen Ordner ein.
- **`ProtectHome`:** Der Ingest hat `read-only`, weil der Zustandsordner der Kopplung
  (`KORE_STATE_DIR`) meist unter dem Home des Bridge-Nutzers liegt; mit `yes` würden Gerätetokens
  still scheitern. Liegt der Zustandsordner außerhalb von `/home`, kannst du auch beim Ingest
  `yes` setzen. Der MCP-Server braucht kein Home und hat `yes`.
- **`226/NAMESPACE`:** Die Mount-Namespaces hinter der Sandbox brauchen in einem unprivilegierten
  System-Container aktivierte Verschachtelung (`nesting`), sonst startet der Dienst nicht. Ohne
  `nesting` entfernst du die Zeilen, die Mount-Namespaces brauchen (`PrivateTmp`, `ProtectSystem`,
  `ProtectHome`, `ProtectKernel*`, `ProtectControlGroups`), und behältst die übrigen.
