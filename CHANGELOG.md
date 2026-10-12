# Changelog

## 2.2.0rc1

Quality, real LLM, deferred features and production readiness. Source: [docs/dev/ITERATION_PLAN_v2.2.md](docs/dev/ITERATION_PLAN_v2.2.md),
review findings in [docs/dev/REVIEW_2026-10-12.md](docs/dev/REVIEW_2026-10-12.md), decisions in [docs/dev/DECISIONS.md](docs/dev/DECISIONS.md).
Operator notes (upgrade, new variables, behaviour changes, limits) are in [docs/dev/RELEASE_NOTES_v2.2.0rc1.md](docs/dev/RELEASE_NOTES_v2.2.0rc1.md).

### Claims and extraction quality

- Residual gate before the heuristic may call a pair `supports` or `duplicate`: subject, metric and qualifier must agree, otherwise the pair goes to the model (DOM-24).
- Numbers: multipliers apply to currency and counts, units normalised, adjectives before a counter noun no longer hide the quantity, and 并未 / 尚未 are negations (DOM-25).
- Dates: month-only English dates are anchored to `asserted_at`; more event words (scheduled, opening, 启动) and update cues (deferred, 原定) (DOM-26).
- Extraction recall: the quantity check runs before the length floor, and more assertion verbs and units are recognised (DOM-27, DATA-23 lock order is in the LLM section).
- The dev evaluation set was cleaned (duplicate labels and rows, qualifier pairs changed to `refines`) and a held-out test set was written and hash-locked before any tuning (EVAL-9). The evaluation runner gained `--split` and `--llm` options; dates now come from each data row. EVAL-10 remains partly open (REVIEW).

### Real LLM through OpenRouter free models, with replay

- OpenRouter preset with a free-model guard in `LLMClient.__init__`: paid models are refused unless `LLM_ALLOW_PAID` is set (default `false`) (K3.1).
- Instance-wide daily request budget, counted under an advisory lock; quota exhaustion (HTTP 429) is not retried and later calls fall back (LLM-9).
- Ask: a model failure mid-stream emits an `error` event, then a heuristic answer, and the answer is persisted once (LLM-8).
- `verify`, belief diff and wiki compile now receive the saved provider settings and budget (LLM-7).
- Claim extraction takes the per-user lock before any LLM call (DATA-23).
- Cassettes: LLM results can be recorded and replayed offline (`LLM_CASSETTE_MODE`) (K1.4, K3.5).

### Retrieval and evaluation

- `make eval-full` switches to fastembed; the SciFact reranker path is `RERANK_PROVIDER=fastembed` (K4). SciFact now has its own report; the README figures are not yet traceable to a committed report (EVAL-11, open).

### Library, schedules and timeline

- Collections, tags, document flags, and document filters (`starred`, `archived`, `source_type`, `q`) (`app/library`, K5.1–K5.2).
- Recurring schedules that queue jobs through the existing queue; `GET /v1/schedules/{id}/runs` lists them (K5.3).
- Timeline `start`/`end` take precedence over `from`/`to` (K5.4).

### Wiki incremental patches

- `app/wiki/patch.py` rewrites only the sections a claim change touched. It falls back to a full compile when the change exceeds `MAX_PATCH_FRACTION` (30%), the output does not parse, the claim count exceeds `MAX_LLM_CLAIMS` (120), or the model is not live (K5.5).
- Revisions record how they were compiled and a section-level change summary (K6.3, K5.7).

### Product pages

- Web: library, inbox (preview before save), wiki history, settings (LLM, token TTL, usage) and schedules pages (K6).
- `lib/types.ts` is derived from the generated OpenAPI types (FE-16).
- Uploaded Markdown takes its title from its first heading (K6).

### Operations and security

- Structured JSON logs (`LOG_FORMAT=json`), request ids, one access line per request, `/metrics` behind `METRICS_TOKEN`, and a `shm_size` fix for Postgres under load (OPS-11, K7.1–K7.2, K7.7).
- Production guard: registration and `/docs` default to off, short secrets and the development database password are refused (SEC-18).
- Security headers on API and web responses (SEC-19).
- JWT lifetime 24 hours (was 168), `POST /v1/auth/logout-all` revokes sessions via `token_version`, and the login limit is kept in Postgres (SEC-19, SEC-20).
- Rate limits for login, ask, verify, capture and upload, off unless `RATE_LIMIT_ENABLED=true` (SEC-20).
- Compose: `env_file`, health checks, a required `POSTGRES_PASSWORD`, and pinned versions. The web build takes `NEXT_PUBLIC_API_URL=/api` for same-origin deployments (OPS-9, OPS-10, K7.4).
- Backup and restore scripts with a round-trip test (OPS-12).
- Upload content check (`%PDF-` magic bytes) and a JWKS negative cache (SEC-21, SEC-22).
- Silent exceptions log a warning; a migration no longer disables application loggers (DOC-11, OPS-13).
- The review citations in the test suite point to tests that exist (TEST-9, K0.4); the DNS fallback for test domains moved out of production code (TEST-10).
- `REQUIRE_DB=1` makes an unreachable database fail the run instead of skipping tests (TEST-11).
- Version is read from one place (DOC-10).

### Briefs, imports, reliability and sharing

- Evidence briefs (Markdown) for answers and topics, through REST and MCP (`export_brief`). Every footnote quote is the exact source slice (K8.1).
- OPML and highlights CSV import (K8.2). Source reliability levels, which the conflict view weights (K8.3).
- Bookmarklet capture card in Settings and a capture preview endpoint (`POST /v1/captures/parse`) (K8.4, K5.7).
- Read-only share links for wiki pages and answers (`SHARING_ENABLED`) (K8.5).

### Closed review items

Counts from [REVIEW_2026-10-12.md](docs/dev/REVIEW_2026-10-12.md) (28 rows: 27 found in the review plus OPS-13 found during execution).

| level | fixed | deferred | open | IDs |
|---|---|---|---|---|
| P1 | 8 | 0 | 1 | fixed: DOM-24, DOM-25, EVAL-9, LLM-7, LLM-8, OPS-9, SEC-18, TEST-11. open: EVAL-10 (partly fixed) |
| P2 | 14 | 1 | 1 | fixed: DATA-23, DOC-10, DOM-26, DOM-27, FE-16, LLM-9, OPS-10, OPS-11, OPS-12, OPS-13, SEC-19, SEC-20, TEST-9, TEST-10. deferred: DATA-24. open: EVAL-11 |
| P3 | 3 | 0 | 0 | fixed: DOC-11, SEC-21, SEC-22 |

### Known gaps in this release

- Claims dev and test targets were not met (dev 0.7899 against 0.80, test 0.6732 against 0.72); the live wiki compile did not return a parseable answer once (PROGRESS K3.4, K3.5; HUMAN_TODO sections six and seven).
- Full list of limits: see [RELEASE_NOTES_v2.2.0rc1.md](docs/dev/RELEASE_NOTES_v2.2.0rc1.md).

## v2.1.0-rc1 — Security, correctness, and evaluation audit release

A comprehensive correctness and security pass resolving the 117 findings identified during the post-v2.0 code audit ([docs/dev/REVIEW_2026-10-04.md](docs/dev/REVIEW_2026-10-04.md)).

### Resolved findings

- **Security & Tenant Isolation.**
  - Fixed cross-tenant leaks in version diff and document claims endpoints (SEC-1).
  - Added strict SSRF `netguard` with private/loopback/cloud metadata IP rejection and DNS pinning (SEC-2).
  - Expanded full-matrix tenant isolation tests across all resources and added CI route coverage guard (SEC-3, H0.4).
  - Enforced token write scope checks on all mutations; distinguished 401 Unauthenticated vs 403 Forbidden with proper `WWW-Authenticate` header (SEC-4, SEC-5).
  - Restricted PAT scope escalation and implemented constant-time login checks (SEC-6).
  - Added file upload chunk streaming with 25MB body limit (SEC-7).
- **Queue & Pipeline Robustness.**
  - Rewrote worker transaction boundary: dequeue commits independently so failing handlers record backoff and respect `max_attempts` without stuck infinite retries (DATA-2).
  - Enabled cancellation and live progress reporting on long-running worker tasks (DATA-3).
  - Enforced transaction-scoped advisory locks (`pg_try_advisory_xact_lock`) with `JobDeferred` re-queuing for concurrency safety (DATA-4).
  - Fixed document identity key collision for notes, uploads, and MCP captures (`file:<sha256>` / `hash:<clean_text>`) (DATA-1, DATA-5).
  - Retained canonical source URLs for Wayback machine backfill snapshots (DATA-6).
  - Added queue summary endpoint (`GET /v1/jobs/summary`) for deterministic queue draining assertions (TEST-4).
- **Claims, Semantics & Temporal Logic.**
  - Rebuilt unified quantity and interval model (`Quantity`/`Unit`) with 84 hand-verified test cases (DOM-1, DOM-8).
  - Refined numeric contradiction logic to distinguish different units from incompatible values (DOM-9).
  - Fixed negation detection to ignore non-negative expressions like "not only" (DOM-7).
  - Resolved pair swapping reversal and enforced user isolation during cross-cluster merging (DOM-3, DOM-13).
  - Replaced Python-level O(n²) cosine scans with pgvector native similarity queries (DOM-22).
  - Partitioned disagreement camps by connected components to eliminate global camp pollution (DOM-11).
  - Guaranteed idempotent document reconciliation preserving cross-document supersession (DOM-2).
  - Fixed cited verification mode to evaluate full chunks and eliminate circular self-validation (DOM-4, DOM-5).
  - Added structured footnote API linking each footnote to its verbatim quote and origin document (DOM-14, DOM-15, FE-5).
- **LLM Integration.**
  - Refactored LLM client with dynamic credential resolution, request deduplication, and streaming `<think>` stripping state machine (LLM-2, LLM-4, LLM-6).
  - Verified StrictUndefined template rendering across all 8 prompt files (TEST-6).
  - Connected LLM claim extraction and relation judging fallback paths (LLM-3).
- **Evaluations & Benchmarks.**
  - Corrected macro-F1 calculation to draw true positives strictly from confusion matrix diagonals (EVAL-1).
  - Removed ground-truth label leakage from claim pair evaluation (EVAL-2).
  - Reconstructed sentence-boundary aligned extraction gold spans (EVAL-3, EVAL-4).
  - Added Latency suite (retrieval and verify p50/p95) and integrated the external SciFact academic benchmark (EVAL-6).
  - Published updated verified numbers: Retrieval nDCG@10 0.892, Verify macro-F1 0.969, Extraction alignment 1.000 / recall 0.754, Claims macro-F1 0.417.
- **Frontend & E2E Testing.**
  - Fixed FormData content-type headers for file uploads (FE-1).
  - Separated quick-capture input routing between URLs and notes (FE-2).
  - Added ClaimStatusBadge for active/carried/superseded/retracted statuses (FE-8).
  - Added Outdated Notes cards and Save as Note action (FE-14).
  - Rebuilt E2E smoke tests into a 13-stage end-to-end journey with `retries: 0` and full queue drain polling (TEST-3, TEST-4, TEST-8).
- **Operations & Tooling.**
  - Fixed portable sed execution in `ports.sh` and hardened process group signal handling (OPS-1, OPS-2).
  - Configured shared Docker volume for API and worker containers, switched container users to non-root, and updated CI configuration (OPS-4, DATA-11).

## v2.0.0-rc1 — Claim-level research

A rewrite. v1 summarised sources; v2 keeps a ledger of what each one *claims*, and
builds four things on top of that.

### Added

- **Claim ledger.** Every sentence a source makes is extracted as an atomic claim with a
  verbatim quote and character offsets. `clean_text[char_start:char_end] == quote` holds
  for every claim; a quote that cannot be located in the source is discarded rather
  than stored.
- **Divergence maps.** Claims that cannot both be true are grouped into opposing camps by
  two-colouring a signed graph. A component that cannot be two-coloured is reported as
  `unbalanced` with its frustrated edges, rather than forced into a false binary.
- **Belief diffs.** What changed over a period, on either the world-time or
  observed-time axis, as a digest in which every bullet cites a claim id that exists in
  the payload.
- **Stale-note detection.** Notes that agree with something since superseded, or that a
  newer source contradicts, are flagged with their evidence. The note itself is never
  edited.
- **Compiled wiki.** Pages build from claims; change a source and only the pages that
  cited it recompile. Every sentence keeps a footnote to its source claim.
- **Four-way verification.** supported / contradicted / disputed / unsupported per
  sentence, with the evidence that decided it. Answers stream with the verdict attached
  as each sentence is read.
- **Agent layer.** Nine provenance-first tools over REST and MCP (Streamable HTTP at
  `/mcp`, plus stdio). See [docs/AGENTS_API.md](docs/AGENTS_API.md).
- **Evaluation harness.** Four suites with hand-checked metrics and a reproducible
  report that states its environment. See [evals/README.md](evals/README.md).

### Changed

- Storage is Postgres only (PostgreSQL 16 + pgvector). SQLite is gone; every index that
  matters — full-text, HNSW, the partial unique index that makes debouncing work —
  needed a real database.
- The job queue is Postgres. Handlers are idempotent and the whole pipeline is
  debounced per scope.
- Retrieval is hybrid: `tsvector` fused with pgvector by reciprocal rank fusion, with an
  optional cross-encoder reranker.

### Removed

- The empty semantic index. v1 computed embeddings and threw them away; v2 either uses
  them or does not compute them.
- The in-process TF-IDF search that read every chunk into Python on each query.
- Supabase as the default auth path. Still supported behind `AUTH_MODE=supabase`.

### Upgrade notes

v1 data is **not** migrated: v2 uses a fresh database volume. Re-import through the
capture and upload endpoints. See
[docs/dev/HUMAN_TODO.md](docs/dev/HUMAN_TODO.md).
