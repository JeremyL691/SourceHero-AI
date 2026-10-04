# Changelog

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
