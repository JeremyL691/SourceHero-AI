# Live LLM full-flow demo report

> Environment: OpenRouter free model `nvidia/nemotron-3-super-120b-a12b:free` (primary; fallbacks `google/gemma-4-31b-it:free`, `openrouter/free`).
> Every number in this report names the command or query that produced it. The numbers come from one complete run of `scripts/live_demo.sh` (2026-10-12, UTC 04:0x–04:31).
> The key exists only in the local `.env`; neither this report nor the recordings contain it.

## 1. How to run

```bash
bash scripts/live_demo.sh
```

The script does four things in order:

1. Reset and migrate the standalone database `sourcehero_live` (`scripts/db_ensure_e2e.py`, `alembic upgrade head`).
2. Seed the demo corpus with the real model (`python -m app.demo.seed`, draining the queue). Every answer is recorded to `evals/cassettes/live_demo.jsonl` (committed); the daily request budget is 300 (`LLM_DAILY_REQUEST_BUDGET=300`).
3. Call `/v1/ask` three times in-process with `scripts/live_demo_probe.py`, writing the results to `.dev/logs/live-demo.json`.
4. Seed the same corpus into `sourcehero_live_fake` with the fake provider as a comparison (no network).

Two problems came up and were fixed; both are described below: the probe's import path (the script now sets `PYTHONPATH=.` and the probe was rerun; the seeding stage was not repeated) and empty sentences in the Ask stream (section 5).

## 2. Requests and timing

| Metric | Value | Source |
|---|---|---|
| Real HTTP requests (this demo) | 41 | OpenRouter account `used` counter: 65 before the run, 106 after |
| Recorded successful answers (cassette lines) | 41 | `wc -l evals/cassettes/live_demo.jsonl` (38 seeding + 3 probe) |
| Seeding time | 1240 s | `scripts/live_demo.sh` prints `live: seed finished in 1240s` |
| `llm_calls` rows (ok / error) | 26 / 8 | `live_report.py` key `llm_calls_by_status` |
| Tokens of successful calls (prompt / completion) | 29 476 / 45 382 | key `llm_ok_tokens` |
| Mean latency of successful calls | 24.6 s | key `llm_ok_tokens` (`mean_latency_ms`) |
| Budget refusals | 0 | key `llm_calls_by_status` (no `budget` rows) |
| 429 and quota exhaustion | 0 | key `llm_failures` (no `quota` rows) |

Note: `llm_calls` records one row per `complete()`, while there were 41 real HTTP requests; the difference comes from repair rounds after a structured-output failure (a second request inside the same row). The 26 ok rows include the probe's two runs (3 rows each); the second run replayed entirely from the recording and made no real requests.

## 3. Claims

| Metric | Value | Source |
|---|---|---|
| Total claims | 94 | key `claims_created_by_extract_jobs` |
| Extracted by the LLM | 49 (about 52%) | key `claims_by_extractor`, `extractor = llm(openai_chat)` |
| Heuristic (LLM fallback) | 45 | key `claims_by_extractor`, `extractor = heuristic` |
| LLM extraction per paragraph | 9 paragraphs succeeded, 7 fell back | key `llm_calls_by_task_and_status` (`claim_extract`: ok 9, error 7) |
| Quote alignment | 0 rejected (`rejected_unaligned` total 0) | key `quotes_rejected_unaligned` |

## 4. Relations, conflicts and the wiki

| Metric | Value | Source |
|---|---|---|
| Total relations | 27 | key `relations_by_method_and_label` |
| `method = llm` relations | 12 (supports 7, contradicts 3, refines 2) | same |
| `method = heuristic` relations | 15 | same |
| Conflicts (contradicts) | 10 (heuristic 7, LLM 3) | key `contradictions` |
| Wiki pages | 3, all clean | key `wiki_pages_by_status_and_compile_method` |
| Compiled by the model | 2 (`llm:nvidia/nemotron-3-super-120b-a12b:free`) | same |
| Heuristic fallback | 1 (the model's second answer failed `WikiBody` validation, `sections.2`) | same; keys `llm_failures` and the `wiki_compile` error row |

## 5. Ask examples (per-sentence verdicts)

From `scripts/live_demo_probe.py`, raw results in `.dev/logs/live-demo.json`; every stream ends with `done`, `method = llm(openai_chat)`, and no `error` events.

1. **What is the energy density of the HX-2?** (8 pieces of evidence retrieved)
   - supported: Halcyon Cells states that the HX-2 cell delivers an energy density of 450 Wh/kg.
   - supported: Independent laboratory testing measured the HX-2 energy density at 380 Wh/kg.
2. **How many tokens was Orchid-7 trained on?**
   - supported: Orchid-7 was trained on 2 trillion tokens.
3. **What launch price did the HX-2 pack have?**
   - disputed: Halcyon Cells expects the HX-2 pack price to be $120/kWh at launch.
   - contradicted: However, Meridian Equity Research projects that first-year HX-2 pack prices will be above $150/kWh.

The answers to all three questions are in the recording; the probe's second run made no new real requests.

**Empty-sentence defect (fixed)**: the model writes citations after the full stop (`… tokens. [1, 2]`); the sentence splitter treated the citation as its own fragment, which is empty once the citation is stripped, so an empty `unsupported` sentence appeared in the stream. Fix: `_emit_new_sentences` in `app/verify/answer.py` skips fragments that are empty after cleanup; regression test `tests/test_ask.py::test_ask_never_emits_an_empty_sentence_for_trailing_whitespace` (failed before the fix: `['…', '…', '']`). After the fix the probe's three streams contain no empty sentences.

## 6. Failures and fallbacks

The 8 error rows in `llm_calls`, grouped by message (key `llm_failures`):

- `empty response`: 5 rows (the model returned nothing; reasoning used up the output budget);
- `response is not a JSON object`: 2 rows, truncated or prose-wrapped answers in claim extraction;
- `WikiBody` validation failure: 1 row, one page fell back to heuristic.

Fallback results: 7 paragraphs fell back to heuristic extraction (section 3); 1 wiki page fell back to heuristic compilation (section 4). Ask did not fall back.

## 7. Comparison with fake mode

Same corpus, fake provider, `sourcehero_live_fake` (command: `uv run python scripts/live_report.py --db-url <sourcehero_live_fake> --cassette none.jsonl --probe none.json`):

| Metric | Real model (live) | fake |
|---|---|---|
| Total claims (key `claims_created_by_extract_jobs`) | 94 (LLM 49 + heuristic 45) | 85 (all `llm(fake)`) |
| Total relations | 27 (LLM 12 + heuristic 15) | 21 (all heuristic) |
| Conflicts (contradicts) | 10 | 7 |
| Wiki pages | 3 (LLM 2 + heuristic 1) | 3 (all heuristic) |
| LLM call rows (`llm_calls`) | 34 (ok 26, error 8) | 24 (ok) |

The fake results are deterministic and say nothing about model quality; the point is that the same code path, under a real model, produces LLM-sourced claims, relations and wiki pages that never appear under fake.

## 8. Limits

- A reasoning model spends the output budget first; the output budgets of claim_extract, verify, relation_judge and wiki_compile were raised to 4000, 6000, 6000 and 6000.
- The live wiki case is still skipped and its replay is a strict xfail. The two LLM-compiled wiki pages in this demo show the output-budget change works, but they do not replace a live check.
- relation_judge misses its targets on dev and test; the relation judgements in this demo carry the same limits.
- This report describes a single run; when the same script runs again, answers already in the recording are replayed and new answers make real requests.
