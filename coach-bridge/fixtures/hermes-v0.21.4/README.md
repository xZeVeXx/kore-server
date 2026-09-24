# Fixtures Hermes v0.21.4 (Gate G1, 24.09.2026)

Echte Ausgaben von Hermes Agent **v0.21.4 (2026.9.21, upstream defb7033)** auf dem Bridge-Host, Profil `<profil>`,
Quelle `kore-probe`. **Nur harmlose Probe-Inhalte** („Antworte nur mit OK"); keine Health-Daten, keine
Geheimnisse. Die Probe-Sessions sind danach gelöscht.

## Befehle

```sh
# stream-general.jsonl
printf 'Antworte nur mit OK' | hermes -p <profil> chat -Q --format stream-json --query-file - \
  --max-turns 2 -t web,skills,memory --source kore-probe
# stream-general-resumed.jsonl (dieselbe Session fortgesetzt)
printf 'Antworte nur mit OK' | hermes -p <profil> chat -Q --format stream-json --query-file - \
  --max-turns 2 -t web,skills,memory --source kore-probe --resume 20260924_085115_defa4e
# stream-resume-unknown.jsonl (nach `hermes -p <profil> sessions delete --yes 20260924_085115_defa4e`)
printf 'Antworte nur mit OK' | hermes -p <profil> chat -Q --format stream-json --query-file - \
  --max-turns 2 -t web,skills,memory --source kore-probe --resume 20260924_085115_defa4e
```

`toolsets-*.txt`: `model_tools.get_tool_definitions(enabled_toolsets=[…])` im Hermes-venv mit
`HERMES_HOME=~/.hermes/profiles/<profil>`, nur lesend (Kommentare mit `#`).

## Befunde für den Bridge-Runner

- Record-Typen in diesen Läufen: `system`/`init`, `text` (Deltas), `result`. `tool_use`/`tool_result`
  kamen in den Probe-Turns nicht vor (kein Werkzeugaufruf).
- Die Session steht im `result`-Record (`session_id`). Beim Resume bleibt die ID gleich. stderr enthält
  zusätzlich `session_id: <id>` und beim Resume eine Zeile mit dem Hermes-Titel — nicht verwenden.
- **Unbekannte Session bei `--resume`:** Der `result`-Record ist unspezifisch, wörtlich:

  ```json
  {"type": "result", "session_id": "20260924_085115_defa4e", "exit_code": 1, "text": "",
   "tokens": {"input": 0, "output": 0, "total": 0, "cache_read": 0, "cache_write": 0},
   "error": "credentials or agent init failed", …}
  ```

  Derselbe Text kommt bei fehlgeschlagener Anmeldung/Agent-Init (`hermes_cli/cli_single_query.py:494`).
  Der eindeutige Text steht nur auf **stderr**, wörtlich:

  ```
  Session not found: 20260924_085115_defa4e
  Use a session ID from a previous CLI run (hermes sessions list).
  ```

  (`hermes_cli/cli_agent_setup_mixin.py:576`). Exit-Code 1, kein Modellaufruf (tokens 0), keine neue
  Session angelegt. Aus dem `result`-Record allein ist „unbekannte Session" nicht von „Anmeldung
  gescheitert" zu unterscheiden — Task 8 muss das entscheiden (stderr-Zeile nur zur Fehlerklassifikation
  oder Existenzprüfung vor dem Resume).
- Kompression ließ sich in v0.21.4 pro Aufruf nicht erzwingen (kein Schalter; `/compress` wirkt im
  Einmal-Modus nicht, die Anfrage ginge als Text ans Modell). Eine Fixture mit abweichender ID in
  `init`/`result` muss synthetisch abgeleitet und als solche markiert werden.

## Ergänzt in Task 8 (Bridge-Runner)

- `stream-resume-unknown.stderr.txt`: die stderr-Zeilen des Laufs `stream-resume-unknown.jsonl`, wörtlich
  aus dem Abschnitt oben übernommen (echte Ausgabe). Die Bridge nutzt stderr **nur** zur Erkennung
  „unbekannte Session" und gibt nichts davon an die App.
- **SYNTHETISCH** (nicht von Hermes erzeugt, nach `hermes_cli/stream_json.py` v0.21.4 gebaut):
  - `synthetic-stream-compressed.jsonl`: abweichende `session_id` in `init` und `result`, wie nach einer
    Kompression (`_sync_cli_session_id_from_agent`). Kompression ließ sich in G1 nicht erzwingen.
  - `synthetic-stream-tool-use.jsonl`: ein `tool_use`-/`tool_result`-Paar (kore-MCP-Name, Ticket im
    `input`, Tool-Ausgabe). Die G1-Probe-Turns riefen kein Werkzeug auf.
