# Ohne Docker: systemd

Die beiden Units sind der Live-Stand der Referenzinstallation (Debian-12-Container).
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

Die Units binden auf `0.0.0.0`, weil der Container nur im eigenen LAN hängt.
Wer den Server auf einem Rechner mit öffentlicher Adresse betreibt, muss
`--host` bzw. `KORE_MCP_HOST` auf die LAN-Adresse setzen und darf die Ports
nicht im Router weiterleiten.

Die `.env` gehört auf `chmod 600` und dem Dienstnutzer. Sie enthält die beiden
Tokens und das Origin-Geheimnis im Klartext.
