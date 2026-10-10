# Architecture

How SourceHero is put together, why, and where it is known to be weak.

---

## The shape of the system

```
capture ─► ingest_source ─► index_version ─► extract_claims ─┬─► cluster_claims ─► judge_relations ─┐
                                                          │                                       │
                                                          └────────► reconcile_document ──────────┤
                                                                                                  ▼
   search ◄── hybrid retrieval ◄── chunks ◄── PostgreSQL (tsvector + pgvector)              plan_topics
                                                                                                  │
   ask / verify ◄── evidence ◄── claims ◄── claim_relations ◄── claim_clusters ◄───────── compile_wiki_page
                                                                                                  │
                                                                   belief_diff ◄── claim_events ◄─┘
```

Four data flows, all in one database:

| flow | direction | purpose |
|---|---|---|
| **ingestion** | source → document → version → chunk | get text into the database, once, with offsets |
| **claims** | chunk → claim → cluster → relation | turn sentences into a checkable ledger |
| **time** | version pairs → claim status + events | track what was superseded, and when |
| **compilation** | claim clusters → topic → wiki page | build prose that rebuilds when inputs change |

---

## Data model

```mermaid
erDiagram
    USER ||--o{ SOURCE : owns
    USER ||--o{ API_TOKEN : authenticates
    SOURCE ||--o{ DOCUMENT : produces
    DOCUMENT ||--o{ DOCUMENT_VERSION : versions
    DOCUMENT_VERSION ||--o{ CHUNK : chunks
    DOCUMENT_VERSION ||--o{ CLAIM : yields
    CHUNK }o--o{ CLAIM : locates
    CLAIM_CLUSTER ||--o{ CLAIM : groups
    CLAIM ||--o{ CLAIM_RELATION : left
    CLAIM ||--o{ CLAIM_RELATION : right
    CLAIM ||--o{ CLAIM_EVENT : emits
    TOPIC ||--o{ WIKI_PAGE : compiles
    WIKI_PAGE ||--o{ WIKI_PAGE_CLAIM : cites
    CLAIM ||--o{ WIKI_PAGE_CLAIM : cited_by
    WIKI_PAGE ||--o{ WIKI_LINK : links
    WATCH }o--|| SOURCE : follows
    BELIEF_DIFF }o--|| USER : reports

    DOCUMENT_VERSION {
        text clean_text "normalized source of truth for every offset"
        timestamptz effective_at "WORLD time: ordering, not insertion"
        char content_hash "unique per document"
    }
    CLAIM {
        text statement "the assertion"
        text quote "clean_text[char_start:char_end] — always"
        int char_start
        int char_end
        varchar status "active|carried|superseded|retracted"
        timestamptz asserted_at "WORLD time of the assertion"
        timestamptz observed_at "SYSTEM time we learned it"
    }
    CLAIM_EVENT {
        varchar event "asserted|superseded|retracted|conflict_opened|conflict_resolved"
        timestamptz at "world time"
        timestamptz observed_at "system time"
    }
```

### The invariant everything rests on

```
version.clean_text[claim.char_start : claim.char_end] == claim.quote
```

Every citation in the interface, every footnote in the wiki and every highlight in the
reader resolves through this. It holds because the quote is **re-read from the stored
text** after alignment, never taken from whatever produced it. A claim whose quote
cannot be located is dropped, not stored.

`tests/test_claims.py::test_every_claim_quote_reproduces_the_source` asserts it across
the whole demo corpus.

---

## Retrieval

Two legs, fused by reciprocal rank fusion.

**Lexical** — `tsvector` over tokenized text. `search_text` is written with jieba for
CJK and a regex for Latin, and Postgres maintains `tsv` as a generated column.

**Vector** — pgvector HNSW over 384-dimensional embeddings.

```
RRF(d) = Σ over lists containing d of  1 / (k + rank_in_that_list)
```

Rank fusion rather than score blending is the key choice: `ts_rank_cd` is unbounded and
cosine distance is a distance. Blending them would require inventing a normalisation
constant; RRF only reads orderings, so both legs contribute on their own scale.

Query parameters are bound through SQLAlchemy's `cosine_distance` operator, not raw
SQL — psycopg3 sends a Python list as `numeric[]`, which makes `<=>` fail to resolve.

**Filters** default to each document's *current* version. A superseded sentence is not
retrievable as present-tense fact unless you ask for history explicitly
(`include_old_versions`).

**Embeddings.** The default `HashingEmbedder` is a signed hashing vectorizer over token
unigrams and bigrams: `blake2b` for the bucket, the high bit for the sign, L2
normalised. It is deterministic, needs no download, and produces identical vectors on
every machine — which is what makes the test suite, the e2e run and the demo
reproducible. It has no notion of synonymy, so unrelated-but-equal sentences score near
zero. `fastembed` is the production choice; switching requires recalibrating
`CLUSTER_SIM` / `PAIR_SIM` / `DUP_SIM` / `TOPIC_SIM`, which are measured per embedder in
`app/claims/cluster.py`.

---

## Claims

### Extraction

Sentences become claims if they have something to be wrong about: a quantity, a date, or
an explicit assertion. Length thresholds differ by script because the units do — three
content tokens is a real English claim ("Orchid-7 has 7 billion parameters.") and
noise in Chinese.

English verbs match by **stem prefix**, not by enumerating inflections, and the search
runs over raw words rather than `content_tokens`: `will`, `is` and `are` are stopwords,
and they are exactly the words that decide whether a sentence is a prediction or an
assertion.

### Alignment

Three strategies, strictest first: exact substring, then normalised (NFKC, curly quotes,
dashes, collapsed whitespace), then fuzzy (rapidfuzz, cutoff 90). Failure at all three
means the quote is invented, and the claim is discarded.

### Clustering

Incremental: a claim joins the nearest cluster whose centroid is close enough, otherwise
it starts one. Two sweeps over each batch, because single-pass assignment is
order-dependent — a claim that arrives before its future cluster-mate creates a rival
cluster and is never reconsidered.

Clusters are also merged when relation judging connects them with high confidence. That
merge is what puts both sides of a disagreement in one place, which the divergence map
needs and clustering alone does not guarantee.

### Relation judging

Two stages. The heuristic decides what it can — same-unit numeric conflict, asymmetric
negation, a date that moved, an update cue — and only unresolved pairs go to the model,
in batches.

Each new claim compares against at most `MAX_JUDGE_PAIRS_PER_CLAIM` (5) candidates
found by embedding search, so ingesting cost is independent of corpus size.

Numbers are extracted with a symbolic-unit fallback. The first version used a
hand-written vocabulary and silently missed most real disagreements: "4.8 MW" and
"5.1 MW" produced no quantities at all, so the judge saw two compatible sentences. The
evaluation caught it; claims accuracy went from 0.338 to 0.450 once units generalised.

### Camps

Two-colouring a signed graph: `supports`/`duplicate`/`refines` are +1 edges,
`contradicts` is −1. BFS from each unvisited node assigns a colour, and any edge whose
sign contradicts the colouring's implication marks the component **unbalanced** with the
frustrated edges listed. Three sources that each contradict the other two cannot be
split, and saying so is more useful than inventing a binary.

---

## Time

`current_version_id` follows the greatest `effective_at`, never the highest id. A
Wayback backfill routinely inserts a version that is *chronologically earlier* than what
you already have; ordering by id would make the archive the live page.

Each claim carries both `asserted_at` (world time) and `observed_at` (system time), and
every event does too. "What changed in June?" and "what did I learn about in June?" are
different questions with different answers.

**Reconciliation** recomputes a document's entire claim lifecycle from its full version
set: `carried` (said again, unchanged), `superseded` (restated with a different value or
date), `retracted` (gone). Recomputing rather than appending is what makes execution
order irrelevant — a backfill that inserts a middle version produces the same lifecycle
as one that arrives last.

The user's own notes are deliberately exempt from `superseded`: the pipeline flags them
as outdated with evidence, but never rewrites what someone wrote.

---

## Compilation

The wiki is a build system.

`input_hash` covers the prompt version, the model, the generator mode and the exact set of contributing
claims, sorted so that scan order cannot change it. When the hash moves the page is
dirty; only dirty pages recompile. The compiler formats structured sections with claims,
and the page API returns structured footnotes exposing verbatim quotes and source links.

The validator runs on every page regardless of what wrote it:

1. A sentence must cite at least one claim, and every id must belong to this page.
2. Every quantity in a sentence must be matched, same unit, within 5%, by a quantity in
   the claims it cites.
3. A generated sentence must overlap its claims enough to be *about* them.

Rule 2 deliberately does **not** use `numbers_conflict`. A history sentence naming
both "18%" and "11%" is correct, and a conflict test would delete the most valuable
sentence on the page.

---

## The job queue

Postgres, with three properties worth naming:

- **`FOR UPDATE SKIP LOCKED`** for dequeue. Two workers never claim the same job.
- **A partial unique index over `status = 'queued'`** on `dedupe_key`. Debounced
  producers collide only while a job is *pending*, so a change arriving mid-run is
  never swallowed by the job already handling it.
- **Advisory locks** around cluster, judge, reconcile, plan and compile. Those handlers
  read other workers' uncommitted rows; without the lock each sees only half the corpus.

Handlers are idempotent. Re-running one must not duplicate rows, which is why writes
key on natural constraints rather than on "has this run before".

---

## Known limits

- The offline relation judge over-calls `supports` on pairs whose difference is
  semantic rather than numeric (see `evals/README.md`).
- The hashing embedder has no synonymy; clustering quality is bounded by it.
- Extraction recall is roughly half the gold spans, by design.
- Claim-level retrieval is not indexed: search returns chunks, and extraction narrows
  them afterwards. Claim-level retrieval is the obvious next step.
