# SourceHero API for agents

Everything here is designed around one property: **a result you cannot trace is a result you cannot use.**
Every search hit carries the quoted text and its character offsets in the source document; every
answer sentence carries a verdict and the evidence behind it; every claim carries a verbatim quote
that can be checked against the original text.

- [Quick start](#quick-start)
- [Authentication](#authentication)
- [MCP](#mcp)
- [REST tools](#rest-tools)
- [Endpoints added in v2.2](#endpoints-added-in-v22)
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

Scopes: `read` for queries, `write` for every mutation (captures, library, schedules, shares, imports,
token and settings writes). A PAT without the required scope gets `403` with `code: insufficient_scope`.
JWTs carry full scope. The first account registered on an instance becomes its administrator;
`Settings` and `/v1/usage` require that role.

`POST /v1/auth/logout-all` (write) revokes every JWT issued before the call by bumping the user's
`token_version`. PATs are not affected. Tests: `tests/test_auth.py::test_logout_all_invalidates_tokens_issued_before_it`,
`tests/test_auth.py::test_jwt_without_token_version_claim_is_rejected`.

---

## MCP

The same ten tools are exposed over the Model Context Protocol (`TOOLS` in `app/mcp/tools.py`;
`tests/test_mcp.py::test_every_tool_is_registered`). Both transports carry the PAT.

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

### `export_brief(kind, id)`

Render an answer (`kind="answer"`, the answer's id) or a topic (`kind="topic"`, the numeric topic id) as a
Markdown evidence brief. Returns `{kind, id, format, markdown}`. Every footnote quote is the exact source
slice (`clean_text[char_start:char_end]`). Tests: `tests/test_briefs.py::test_mcp_export_brief_returns_markdown_with_question`,
`tests/test_briefs.py::test_every_footnote_quote_is_the_source_slice`.

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

## Endpoints added in v2.2

Every path below is in `dashboard-web/openapi.json` (85 paths; regenerate with `make openapi`). The
scope column is what the route's dependency requires: `read`, `write`, or `admin` (administrator).
The last column names the test module or function that exercises the route. All paths are under `/v1`
unless marked otherwise.

**Library** (`app/library/service.py`, `app/api/routers/library.py`)

| method | path | scope | behaviour | proven by |
|---|---|---|---|---|
| GET | `/collections` | read | List your collections | `tests/test_library_api.py::test_collection_crud` |
| POST | `/collections` | write | Create a collection; a duplicate name is `409 name_taken` | `tests/test_library_api.py::test_duplicate_collection_name_is_a_conflict` |
| GET | `/collections/{id}` | read | One collection; another user's id is `404` | `tests/test_library_api.py::test_another_tenants_collection_is_not_found` |
| PATCH | `/collections/{id}` | write | Rename or describe | `tests/test_library_api.py::test_collection_crud` |
| DELETE | `/collections/{id}` | write | Delete the collection; its documents are kept | `tests/test_library_api.py::test_collection_crud` |
| POST | `/collections/{id}/items` | write | Add a document; another tenant's document is `404` | `tests/test_library_api.py::test_adding_another_tenants_document_is_not_found` |
| DELETE | `/collections/{id}/items` | write | Remove a document from the collection | `tests/test_library_api.py::test_collection_items_add_count_and_remove` |
| GET | `/tags` | read | List tags | `tests/test_library_api.py::test_tag_create_list_and_duplicate` |
| POST | `/tags` | write | Create a tag (`201`); duplicate is `409` | `tests/test_library_api.py::test_tag_create_list_and_duplicate` |
| DELETE | `/tags/{id}` | write | Delete a tag (`204`) | `tests/test_library_api.py::test_tagging_a_document_and_deleting_the_tag_cascades` |
| POST | `/documents/{id}/tags` | write | Attach a tag to a document | `tests/test_library_api.py::test_tagging_another_tenants_document_is_not_found` |
| DELETE | `/documents/{id}/tags` | write | Detach a tag from a document | `tests/test_library_api.py::test_tagging_a_document_and_deleting_the_tag_cascades` |
| PATCH | `/documents/{id}/flags` | write | Star or archive; only the fields sent are changed | `tests/test_library_api.py::test_flags_patch_sets_only_the_sent_fields` |
| GET | `/documents` | read | Filters `starred`, `archived`, `source_type` and `q` (title or URL; `%` and `_` match literally) | `tests/test_documents_filters.py::test_starred_filter`, `::test_text_search_treats_percent_literally` |

**Schedules** (`app/jobs/schedules.py`, `app/api/routers/schedules.py`)

| method | path | scope | behaviour | proven by |
|---|---|---|---|---|
| GET | `/schedules` | read | List schedules | `tests/test_schedules_api.py::test_create_returns_schedule_with_next_run_at` |
| POST | `/schedules` | write | Create; returns `next_run_at`. Unknown job type and invalid cron fields are `422` | `tests/test_schedules_api.py::test_create_returns_schedule_with_next_run_at`, `::test_invalid_cron_fields_are_422`, `::test_unknown_job_type_is_422` |
| PATCH | `/schedules/{id}` | write | Update. Fields sent as `null` are ignored, so a weekly schedule cannot be changed back to daily (see the known limits in RELEASE_NOTES_v2.2.0rc1.md) | `tests/test_schedules_api.py` (module) |
| DELETE | `/schedules/{id}` | write | Delete a schedule | `tests/test_schedules_api.py` (module) |
| GET | `/schedules/{id}/runs` | read | Jobs queued by this schedule's ticks, newest first. The queue is the run history | `tests/test_schedules_api.py::test_runs_only_list_this_schedules_jobs` |

**Captures and imports** (`app/imports/`, `app/api/routers/sources.py`, `app/api/routers/imports.py`)

| method | path | scope | behaviour | proven by |
|---|---|---|---|---|
| POST | `/captures/parse` | read | Preview a capture (text, PDF, Markdown or HTML): sentence, candidate-claim and page counts, and the first heading as title. Stores nothing and enqueues nothing | `tests/test_captures_preview.py::test_text_preview_does_not_store_or_enqueue`, `::test_pdf_preview_extracts_text_without_storing` |
| POST | `/imports/opml` | write | OPML subscription list becomes RSS sources; duplicates are skipped; private-network URLs are reported, not created | `tests/test_imports.py::test_opml_creates_sources_and_skips_duplicates`, `::test_opml_private_network_url_is_reported_not_created` |
| POST | `/imports/highlights` | write | Highlights CSV becomes notes, deduplicated by highlight text; re-importing skips them | `tests/test_imports.py::test_importing_same_highlights_twice_skips_second_run` |

**Briefs** (`app/briefs/render.py`, `app/api/routers/briefs.py`)

| method | path | scope | behaviour | proven by |
|---|---|---|---|---|
| GET | `/answers/{id}/export?format=md` | read | An answer as a Markdown evidence brief, sent as an attachment | `tests/test_briefs.py::test_export_headers_are_markdown_attachments` |
| GET | `/topics/{id}/brief?format=md` | read | A topic as a Markdown evidence brief; another tenant's topic is `404` | `tests/test_briefs.py::test_other_tenant_gets_404_for_answer_and_topic` |

**Sharing** (`app/sharing/service.py`, `app/api/routers/sharing.py`)

| method | path | scope | behaviour | proven by |
|---|---|---|---|---|
| POST | `/shares` | write | Create a read-only link for a wiki page or an answer. The token is returned once | `tests/test_sharing.py::test_token_is_returned_once_and_never_listed` |
| GET | `/shares` | read | List your links; tokens are never listed | `tests/test_sharing.py::test_token_is_returned_once_and_never_listed` |
| DELETE | `/shares/{id}` | write | Revoke a link (`204`); the public read then returns `404` | `tests/test_sharing.py::test_revoked_share_returns_404_on_public_read`, `::test_other_tenant_cannot_revoke` |
| GET | `/public/shares/{token}` | none (the token is the credential) | Read the snapshot. Returns `404` when revoked, expired, or when `SHARING_ENABLED=false`. Rate limited per client host | `tests/test_sharing.py::test_public_snapshot_has_no_owner_fields`, `::test_expired_share_returns_404_on_public_read`, `::test_disabled_sharing_returns_404_on_every_route` |

**Auth, usage and settings**

| method | path | scope | behaviour | proven by |
|---|---|---|---|---|
| POST | `/auth/logout-all` | write | Revokes every JWT issued before the call (`token_version`) | `tests/test_auth.py::test_logout_all_invalidates_tokens_issued_before_it` |
| GET | `/usage` | admin | Token usage by day, task and model. The `today` block shows requests and tokens counted against the daily budget | `tests/test_settings_ttl_budget.py::test_usage_today_counts_only_todays_calls` |
| GET | `/settings/llm` | admin | Returns `token_ttl_seconds` for the active command credential | `tests/test_settings_ttl_budget.py::test_command_credential_ttl_is_reported_by_settings` |
| POST | `/settings/llm/credentials` | admin + write | For `kind=command`, an optional `ttl_seconds` from 0 to 86400 is stored with the credential. Anything else is rejected with code `bad_ttl` | `tests/test_settings_ttl_budget.py::test_out_of_range_or_non_integer_ttl_is_rejected` |
| GET | `/timeline` | read | `start`/`end` win over `from`/`to` when both are given. `axis=world` or `observed` | `tests/test_filters_k54.py::test_timeline_start_end_win_over_from_to`, `tests/test_temporal.py::test_timeline_observed_axis_differs_from_world` |

**Operations** (outside `/v1`)

| method | path | scope | behaviour | proven by |
|---|---|---|---|---|
| GET | `/healthz` | none | Liveness; fixed body | `tests/test_health.py::test_healthz_is_the_exact_documented_body` |
| GET | `/readyz` | none | Database, embedder and LLM status. Degraded returns `503` with a sanitised detail | `tests/test_health.py::test_readyz_reports_database_embedder_and_llm`, `tests/test_security_hardening.py::test_readyz_degraded_returns_503_and_sanitizes_detail` |
| GET | `/metrics` | bearer `METRICS_TOKEN` | Prometheus text. `404` when `METRICS_TOKEN` is unset, `401` for a wrong token | `tests/test_metrics.py::test_metrics_is_404_when_no_token_is_configured`, `::test_metrics_is_401_without_or_with_wrong_bearer_token`, `::test_metrics_is_200_with_prometheus_text_for_correct_token` |

Login (`POST /v1/auth/login`) and the other rate-limited routes return `429` with `Retry-After` when
`RATE_LIMIT_ENABLED=true` and the window is full. The login limit is keyed by email, so one client can
lock out a known address for a window. Proven by `tests/test_ratelimit.py::test_429_carries_retry_after_header`
and `::test_limit_is_per_email`.

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
   `filters.include_old_versions: true` to query full version history; MCP defaults to current versions).
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
