# Changelog

## Unreleased — v2.1 (in progress)

A correctness and security pass driven by the 2026-10-04 code review of v2.0.0-rc1
([docs/dev/REVIEW_2026-10-04.md](docs/dev/REVIEW_2026-10-04.md)). Plan:
[docs/dev/ITERATION_PLAN_v2.1.md](docs/dev/ITERATION_PLAN_v2.1.md).

### Known issues in v2.0.0-rc1 (being fixed)

- **Security.**
  - The document diff endpoint can return another user's text (SEC-1).
  - Fetching has no SSRF guard (SEC-2).
  - Admin routes ignore token scopes (SEC-4).
  - Auth failures return 403 rather than 401 (SEC-5).
- **Data integrity.**
  - Every upload, note and saved answer without a URL shares one identity key, so they
    overwrite each other as versions of one document (DATA-1).
  - Failed jobs never use up their attempts, and heartbeat, reaper and cancel don't work
    (DATA-2, DATA-3).
  - Reconciliation undoes supersession by other documents (DOM-2).
  - Relation direction is stored reversed for swapped pairs (DOM-3).
- **Claims.** Numbers without a recognised unit are ignored, so "900 participants"
  versus "612 participants" is judged *supported* (DOM-1).
- **LLM mode.** Ask is extractive only, and its sentences are verified against the
  chunk they were copied from. LLM extraction, relation judging and topic labels are not
  implemented. The verify prompt cannot render (DOM-4, LLM-1, LLM-3).
- **Evals.** The macro-F1 computation is wrong and the claims suite leaks labels, so the
  published F1 numbers were withdrawn (EVAL-1, EVAL-2, EVAL-3).
  - In v2.1 H5, evaluation harness was comprehensively audited and corrected:
    - Fixed `per_class_report` to compute true positives strictly from confusion matrix diagonal (EVAL-1).
    - Removed gold label and phenomenon leakage from claims evaluation suite, calculating real-time cosine similarity via embedder (EVAL-2).
    - Rebuilt extraction gold spans on sentence splitting boundaries (EVAL-3, EVAL-4).
    - Guaranteed $nDCG \le 1.0$ via document deduplication (EVAL-7).
    - Added Latency suite and external SciFact benchmark suite (EVAL-6).
    - New audited figures vs withdrawn v2.0 figures:
      - Claims macro-F1: 0.548 (invalid) -> 0.417 (leak-free)
      - Extraction alignment recall: 0.525 (corrupt spans) -> 0.754 (sentence-aligned)
      - Verify macro-F1: 0.753 (metric bug) -> 0.969
      - Retrieval nDCG@10: 0.913 (duplicate bug) -> 0.892 (deduplicated)
- **UI.**
  - Uploads always fail with 422 (FE-1).
  - Quick capture submits free text as a URL (FE-2).
  - Every active claim is shown as "Unsupported" (FE-8).
  - Wiki footnotes map to the wrong claims (FE-5).

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
