# Changelog

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
