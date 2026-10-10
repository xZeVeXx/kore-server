# Ohne Docker: systemd

Die beiden Units stammen aus der Referenzinstallation (Debian-12-Container).
Sie erwarten den Code unter `/opt/kore-server`, ein venv unter `/opt/kore-server/.venv`
und eine `/opt/kore-server/.env` nach dem Muster der `.env.example` im Repo-Wurzelverzeichnis.

```bash
sudo useradd --system --home /opt/kore-server kore
sudo mkdir -p /opt/kore-server && sudo chown kore /opt/kore-server
# server/ aus diesem Repo nach /opt/kore-server kopieren, dann:
sudo -u kore python3 -m venv /opt/kore-server/.venv
sudo -u kore /opt/kore-server/.venv/bin/pip install -r /opt/kore-server/requirements.txt

sudo install -m 600 -o kore .env /opt/kore-server/.env   # aus .env.example erzeugt
sudo cp kore-ingest.service kore-mcp.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now kore-ingest kore-mcp
```

`KORE_DB_PATH` in der `.env` auf einen absoluten Pfad setzen, den der Nutzer `kore`
schreiben darf, z. B. `/opt/kore-server/kore.db`.

Keine Unit bindet auf `0.0.0.0`. Der Ingest lauscht auf `KORE_BIND_ADDRESS` aus der
`.env`, Standard `127.0.0.1`; damit dein iPhone ihn erreicht, trägst du dort die
LAN-Adresse dieses Rechners ein (z. B. `KORE_BIND_ADDRESS=10.0.0.10`). Der MCP-Server
lauscht auf `KORE_MCP_HOST`, Standard `127.0.0.1` — richtig, wenn dein Hermes-Agent auf
demselben Rechner läuft. Die Ports nie im Router weiterleiten.

Nur **im Docker-Container** lauschen beide Dienste auf `0.0.0.0` (`Dockerfile`,
`docker-compose.yml`); nach außen veröffentlicht Compose den Ingest ausschließlich auf
`KORE_BIND_ADDRESS` und den MCP-Server ausschließlich auf `127.0.0.1`.

Die Units warten auf `network-online.target` und geben nie auf
(`StartLimitIntervalSec=0`, `RestartSec=5`): Fehlt beim Booten die LAN-Adresse noch
(„Cannot assign requested address", Errno 99), startet systemd den Dienst alle fünf
Sekunden neu, bis sie da ist.

Die `.env` gehört auf `chmod 600` und dem Dienstnutzer. Sie enthält die beiden
Tokens und das Origin-Geheimnis im Klartext. Solange einer dieser Werte leer ist oder
noch mit `change-me` aus der `.env.example` beginnt, starten Ingest und MCP nicht;
die Meldung im Journal nennt den Schlüssel, nie den Wert.

Alle drei Units setzen `NoNewPrivileges=yes`: Weder die Dienste noch ein von der Bridge
gestartetes Hermes können per `sudo` oder setuid mehr Rechte bekommen als ihr Dienstnutzer.
Wer weiter härten will, ergänzt für Ingest und MCP `PrivateTmp=yes`, `ProtectSystem=full` und
`ProtectHome=yes` (die Datenbank muss dann außerhalb von `/usr`, `/etc` und `/home` liegen, etwa
unter `/opt/kore-server`). Diese drei brauchen Mount-Namespaces; in einem unprivilegierten
System-Container klappt das nur mit aktivierter Verschachtelung (`nesting`), sonst startet der Dienst
mit `226/NAMESPACE` nicht.
