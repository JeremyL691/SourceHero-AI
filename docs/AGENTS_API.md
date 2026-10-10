# SourceHero API for agents

Everything here is designed around one property: **a result you cannot trace is a result you cannot use.**
Every search hit carries the quoted text and its character offsets in the source document; every
answer sentence carries a verdict and the evidence behind it; every claim carries a verbatim quote
that can be checked against the original text.

- [Quick start](#quick-start)
- [Authentication](#authentication)
- [MCP](#mcp)
- [REST tools](#rest-tools)
- [How the verdicts work](#how-the-verdicts-work)

---

## Quick start

```bash
# 1. Start the stack
make dev

# 2. Create a token (Settings -> Tokens & MCP, or)
TOKEN=$(curl -s -X POST http://127.0.0.1:$API_PORT/v1/auth/register \
  -H 'content-type: application/json' \
  -d '{"email":"me@example.com","password":"a-long-password"}' | jq -r .access_token)

PAT=$(curl -s -X POST http://127.0.0.1:$API_PORT/v1/tokens \
  -H "Authorization: Bearer $TOKEN" -H 'content-type: application/json' \
  -d '{"name":"agent"}' | jq -r .token)

# 3. Ask a question
curl -s -X POST "http://127.0.0.1:$API_PORT/v1/ask?stream=false" \
  -H "Authorization: Bearer $PAT" -H 'content-type: application/json' \
  -d '{"question":"What is the energy density of the HX-2?"}' | jq
```

Ports come from `.env` (`API_PORT`, `WEB_PORT`); the examples use `127.0.0.1` throughout.

---

## Authentication

Every endpoint accepts either a JWT (from `/v1/auth/login`) or a personal access token. **MCP accepts
only a PAT** — a JWT is short-lived, and an agent cannot refresh one.

```
Authorization: Bearer sh_pat_...
```

Scopes: `read` for queries, `write` for `capture`. The first account registered on an instance
becomes its administrator; `Settings` endpoints require that role.

---

## MCP

The same nine tools are exposed over the Model Context Protocol. Both transports carry the PAT.

### Streamable HTTP

The endpoint is **`/mcp`** — not `/mcp/mcp`. A request to any other path returns 404, and a request
without a valid token returns 401.

```bash
curl -s http://127.0.0.1:$API_PORT/mcp \
  -H "Authorization: Bearer $PAT" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{
        "protocolVersion":"2025-06-18","capabilities":{},
        "clientInfo":{"name":"probe","version":"1"}}}'
```

**Claude Code**

```bash
claude mcp add --transport http sourcehero http://127.0.0.1:$API_PORT/mcp --header "Authorization: Bearer $PAT"
```

**OpenCode** (`opencode.json`)

```json
{
  "mcp": {
    "sourcehero": {
      "type": "remote",
      "url": "http://127.0.0.1:8787/mcp",
      "headers": { "Authorization": "Bearer sh_pat_..." }
    }
  }
}
```

### stdio

For clients that launch the server themselves. The token comes from the environment; **the process
must be started with the same database as the API.**

```toml
# ~/.codex/config.toml
[mcp_servers.sourcehero]
command = "uv"
args = ["run", "--directory", "/path/to/SourceHero-AI", "python", "-m", "app.mcp"]
env = { SOURCEHERO_TOKEN = "sh_pat_...", DATABASE_URL = "postgresql+psycopg://..." }
```

Everything on **stdout** is the protocol, so the server logs to stderr only.

---

## Tools

### `search_sources(query, k=8, source_types?, date_from?, date_to?)`

Find what the corpus says about a topic. `source_types` filters by kind (`webpage`, `rss`, `pdf`,
`file`, `note`, `clip`); dates are ISO.

Returns hits with `title`, `url`, `source`, `date`, `text`, `char_start`, `char_end` and the
per-leg scores.

### `get_passage(chunk_id, context_chars=400)`

One passage plus surrounding text, with offsets in the full document. Use after a search hit when
the relevant sentence may sit just outside the chunk.

### `ask(question, k=8)`

Answer from the corpus with a verdict per sentence. See [verdicts](#how-the-verdicts-work).

### `verify(text)`

Check one or more sentences against the evidence. Returns a verdict per sentence with the evidence
that decided it. Use this for a claim you wrote yourself.

### `list_conflicts(topic?, limit=10)`

Where sources contradict each other, as two-sided camps. Each camp lists its claims with quote,
source, date and offsets, so a disagreement can be shown rather than summarised.

### `what_changed(since, until?, topic?)`

What moved over a period, as a Markdown digest. Windows longer than 90 days are clamped. Every
bullet cites `[c:id]` claim ids that exist in the returned payload.

### `read_wiki(slug)` / `search_wiki(query, limit=10)`

Read compiled wiki pages. A page is generated from claims: every sentence is a source statement with
a footnote, and `claim_ids` lists what it was built from.

### `capture(url_or_text, title?)`

Save a URL or a note into the library. **Needs the `write` scope.** A URL is queued for ingest; text
becomes a note.

---

## REST equivalents

Every tool is also a plain HTTP endpoint, so an agent that cannot speak MCP can still use the same
functionality.

```bash
API=http://127.0.0.1:$API_PORT/v1
AUTH="Authorization: Bearer $PAT"

# Search
curl -s -X POST $API/search -H "$AUTH" -H 'content-type: application/json' \
  -d '{"query":"energy density","k":5}'

# Verify
curl -s -X POST $API/verify -H "$AUTH" -H 'content-type: application/json' \
  -d '{"text":"Orchid-7 was trained on 2 trillion tokens."}'

# Conflicts
curl -s "$API/conflicts?limit=10" -H "$AUTH"

# Belief diff
curl -s -X POST $API/diffs -H "$AUTH" -H 'content-type: application/json' \
  -d '{"start":"2026-05-01T00:00:00Z","end":"2026-09-01T00:00:00Z"}'

# Wiki
curl -s "$API/wiki/pages" -H "$AUTH"
curl -s "$API/wiki/pages/hx-2" -H "$AUTH"

# Claims and clusters
curl -s "$API/claims?status=superseded" -H "$AUTH"
curl -s "$API/clusters?conflicted=true" -H "$AUTH"

# Timeline
curl -s "$API/timeline?axis=world&limit=50" -H "$AUTH"
```

Streaming answers come back as SSE:

```bash
curl -N -X POST $API/ask -H "$AUTH" -H 'content-type: application/json' \
  -d '{"question":"What is the energy density of the HX-2?"}'
```

Event order is `retrieval` → `conflicts` → (`delta` | `sentence`)* → `done`. Each `sentence` event
already carries that sentence's verdict, so a claim can be checked while it is being read.

---

## How the verdicts work

| verdict | meaning |
|---|---|
| `supported` | evidence restates the sentence |
| `contradicted` | evidence reports a different value or date, or denies it |
| `disputed` | **some evidence supports it and some contradicts it** |
| `unsupported` | no evidence addresses it |

`disputed` is the interesting one: it is not a synonym for "the sources disagree somewhere". It means
this specific sentence has evidence on both sides, which is what a disagreement map is built from.

Three details worth relying on:

1. **Quotes are never paraphrased on the way in.** A claim's quote is re-read from the stored document
   after locating it, so `document.text[char_start:char_end] == quote` always holds. A quote that cannot
   be located is discarded rather than stored.
2. **Superseded claims are excluded from default search.** Retrieval returns each document's current
   version, so an outdated sentence cannot be cited as present-tense fact. (REST `/v1/search` supports
   `include_old_versions=true` to query full version history; MCP defaults to current versions).
3. **Contradiction needs comparable quantities.** "450 Wh/kg" and "$120/kWh" are *unrelated*, not
   disagreeing — a different unit is a different measurement, and treating it as agreement would make
   every unrelated pair support every other.

---

## Limits and failure

- Text over 8000 characters is rejected by `/v1/verify`.
- `what_changed` clamps windows longer than 90 days.
- A failed LLM call degrades to the heuristic path, and the result says so in its `method` field
  (`heuristic(fallback:<code>)`) rather than silently pretending a model wrote it.
- Errors reach an agent as data: MCP tool failures come back as `{"error": ...}` payloads, not as a
  dropped connection.
