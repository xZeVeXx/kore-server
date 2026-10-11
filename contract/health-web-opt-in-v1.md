# Web search in the health chat, on explicit request (v1)

Capability: `health_web_opt_in_v1` (bridge `/capabilities` and `/health`). A bridge of this version
always reports it. It is **optional** for the app: it is not in
[`app-required-capabilities.json`](app-required-capabilities.json); an app that does not see it hides
the switch and never sends the field.

By default a health chat has **no** web search: the tools are `skills,memory,kore` (`skills,memory`
without an origin ticket). A person can switch web search on for the health chat as their own,
explicit choice, at their own risk. It is off unless the request says otherwise.

## Request field

`health_web_allowed`: optional JSON boolean on `POST /coach`, for the synchronous request and for the
turn start (`client_message_id`) alike.

| Request | Tools (`-t`) |
| --- | --- |
| health scope (`health_context_allowed: true`, `consent_scope_version: 2`), field absent or `false` | `skills,memory,kore` (without origin ticket: `skills,memory`) |
| health scope, `health_web_allowed: true` | `web,skills,memory,kore` (without origin ticket: `web,skills,memory`) |
| general scope (`health_context_allowed: false`), any boolean | `web,skills,memory`, unchanged: the field is **ignored** there (the general chat has web search anyway) |
| `POST /coach/adjust-today`, field present with any value | `400`, nothing runs: the adjustment of today's workout never gets `web` |
| any value that is not a JSON boolean (`"true"`, `1`, `null`, …) on `POST /coach` | `400 {"error": "health_web_allowed muss ein JSON-Boolean sein"}`, nothing runs |

The field does not replace the consent: `health_context_allowed: true` still needs
`consent_scope_version: 2` (otherwise `403`), and `health_web_allowed: true` without
`health_context_allowed: true` has no effect.

## Prompt

Without the field (or with `false`, or in the general scope) the prompt is byte for byte what it was
before this capability. With `true` in the health scope the bridge appends one fixed sentence after
everything else, separated by a blank line, in the style of its other authoritative directives:

```
[KORE-WEBSUCHE — BRIDGE-AUTORITATIV] Websuche ist aktiv: Übernimm nie Gesundheitswerte, Namen, Notizen oder Planinhalte in Suchanfragen oder URLs; suche nur allgemein formuliert.
```

The prompt still begins with the fixed first line `[KORE-APP-NACHRICHT]`, and the size limit counts
the sentence (a turn start that only exceeds the limit with it is `413` before the `202`). This is a
soft, non-enforcing mitigation: it asks the coach to keep health values out of queries, it does not
stop a manipulated page from talking the coach out of it.

## Sessions

The signed session reference binds scope and chat, not the tool list. The field may change from one
turn of a health chat to the next, in either direction: the session continues (`context:
"continued"`), no reset, only `-t` differs for that turn.

Because the session continues, a turn with `health_web_allowed: true` in the middle of a chat runs
with the **earlier turns of that health chat** in front of the coach, including the health context
they carried, not only with the current message. Switching web search on mid-chat therefore exposes
the whole conversation so far to the web-enabled run. An app that wants only the current question
next to `web` starts a new chat (no `session`) for it.

## What `/health` reports

`app_chat_toolsets` keeps the keys `general` and `health` (the default, never with `web`) and adds
`health_with_web`, the list for the explicit request, in the same list format:

```json
"app_chat_toolsets": {
  "general": ["web", "skills", "memory"],
  "health": ["skills", "memory", "kore"],
  "health_with_web": ["web", "skills", "memory", "kore"]
}
```

`scripts/kore-doctor` fails when `health` contains `web`, when `health_with_web` contains more than
`health` plus `web`, or when the capability and the list do not appear together; it accepts a bridge
that reports neither (older bridges).

## Risk

With web search in a health chat, the health context in the prompt and the `kore` tools stand next to
a tool that can fetch addresses. A manipulated page that the coach reads could talk it into sending
health values to an address of its choosing. That is why it is off by default, why the person turns it
on knowingly, and why the adjustment of today's workout never has it. Switched on in the middle of a
chat, it exposes the earlier turns of that chat as well (see *Sessions*). The prompt sentence above
lowers the risk, it does not remove it. See *Residual risks* in the README.
