# Snapshot v2: steps, body, nutrition

Capabilities: `activity_body_nutrition_v1` and `apple_hrv_v1` (ingest `GET /capabilities`). Both are
**optional** for the app and not in [`app-required-capabilities.json`](app-required-capabilities.json),
which lists what the app needs from the bridge. An app that does not see them treats the ingest as an
older server: it keeps syncing and leaves out the fields the capability stands for.

## `GET /capabilities` (ingest)

On the ingest API, with the same bearer as `POST /ingest`: the static ingest token or a paired
device's token ([`pairing-v1.md`](pairing-v1.md)). No request body.

| Request | Answer |
| --- | --- |
| valid token | `200 {"snapshot_capabilities": ["activity_body_nutrition_v1", "apple_hrv_v1"]}`, names sorted |
| missing or wrong token | `401 {"detail": "Unauthorized"}`, as on every protected route |
| valid token, ingest v0.13.0 | `404`: the route does not exist there |

`GET /health` stays anonymous and exactly `{"ok": true}`; it names no capabilities.

The app reads `/capabilities` before every sync run and uses the answer for that run only (no cache
across runs). Any other answer than a 200 with this JSON object (404, 5xx, a network error, invalid
JSON, a body larger than 4 KiB) means "no capabilities"; the sync still runs.

| Capability | Without it, the app … | With it, the app … |
| --- | --- | --- |
| `apple_hrv_v1` | leaves out `hrv_kind`, `hrv_method_effective` and `hrv_source` when one of them is `appleRMSSD` or `appleRecoveryRMSSD`; `hrv_ms` and every other field stay | sends them |
| `activity_body_nutrition_v1` | sends none of `steps`, `body`, `nutrition` | always sends `steps` and `body` (possibly empty) and `nutrition` (empty while nutrition sharing is off or Health access to it is missing) |

## Lists in `POST /ingest`

Three optional top-level keys. Each is a JSON array of at most **31** entries with at most one entry
per `date`. `date` is the local calendar day as `YYYY-MM-DD`. Unknown fields are rejected.
`null` is never a value: leave the key out, for a whole list as well as for an optional field.

| List | Entry | Allowed values (inclusive) |
| --- | --- | --- |
| `steps` | `date`, `count` (integer): the day's total | `count` 0 … 200 000 |
| `body` | `date`, `weight_kg`, `body_fat_pct` (optional): the day's last measurement | `weight_kg` 20 … 400; `body_fat_pct` 1 … 75 |
| `nutrition` | `date`, `kcal`, `protein_g`, `carbs_g`, `fat_g`: the day's totals; each value is optional, at least one is present | `kcal` 0 … 20 000; `protein_g`, `carbs_g`, `fat_g` 0 … 2 000 |

- Units: `weight_kg` in kilograms, `body_fat_pct` in percent (`18.0` for 18 %, not HealthKit's
  fraction `0.18`), `kcal` in kilocalories, the rest in grams.
- `count` is a JSON integer (`8000`, not `8000.0`). The other values are JSON numbers; whole numbers
  are fine (`80` for 80 kg). Booleans, strings, `NaN` and `Infinity` are rejected.
- Example:

```json
{
  "generated_at": "2026-06-30T07:00:00+02:00",
  "steps": [{"date": "2026-06-29", "count": 8412}],
  "body": [{"date": "2026-06-29", "weight_kg": 80.4, "body_fat_pct": 18.5}],
  "nutrition": [{"date": "2026-06-29", "kcal": 2350, "protein_g": 140, "carbs_g": 250, "fat_g": 80}]
}
```

One invalid entry rejects the whole snapshot with `422 {"detail": "Invalid request"}` (no input is
echoed) and leaves the stored snapshot as it was. A sender therefore leaves out an entry whose value
falls outside these ranges instead of sending it. The entries count towards the snapshot's limit of
20 000 collection elements.

## Storage

Each list on its own:

| In the snapshot | Stored list |
| --- | --- |
| list with entries | replaced by it |
| `[]` | cleared |
| key absent | kept as stored |

So an app without snapshot v2 (0.13) and a sync without the capability never wipe the lists, and an
app that switches nutrition sharing off sends `"nutrition": []`: from then on the coach sees no
nutrition, also after later snapshots without the key.

## MCP (read only)

| Tool | Returns |
| --- | --- |
| `get_activity_body_trend(days=30)` | `{"steps": [...], "body": [...]}` |
| `get_nutrition_trend(days=30)` | `{"nutrition": [...]}` |
| `get_coach_briefing` | additionally `"activity_body_7d": {"steps": [...], "body": [...]}` and `"nutrition_7d": [...]` |

Entries are returned as stored, sorted by `date`, newest last. `days` is bounded to 1 … 30 and counts
entries, i.e. days with a value; the briefing returns the last 7. A list that was never sent or was
cleared comes back as `[]`.

## Compatibility

- App 0.13 against an ingest v0.14.0: accepted unchanged; stored lists stay.
- App 0.14 against an ingest v0.13.0: `/capabilities` answers 404, the app sends none of the lists and
  no Apple HRV provenance, and v0.13.0 accepts the snapshot.

## Privacy

The lists go only to the user's own ingest. The ingest does not log snapshot values and does not echo
them in errors.
