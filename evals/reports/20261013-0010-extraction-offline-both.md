# Evaluation report

- **mode**: `offline`
- **suites**: `extraction`
- **generated**: 2026-10-13T00:10:13+00:00
- **git**: `6cde4da`
- **python**: 3.12

## Environment

| setting | value |
|---|---|
| embedder | `hashing-v1` (dim 384) |
| embedder detail | `hashing-v1` |
| reranker | `none` |
| reranker detail | `rerank_provider is none` |
| llm provider | `openrouter` (provider mode `cassette`) |
| eval llm mode | `replay` (model `nvidia/nemotron-3-super-120b-a12b:free`, requests 0, cassette `evals/cassettes/extraction-dev.jsonl, evals/cassettes/extraction-test.jsonl`) |
| split | `both` |
| dirty tree | `False` |

## extraction

| metric | value |
|---|---|
| passages | 15 |
| gold_spans | 61 |
| predicted_spans | 63 |
| alignment_rate | 1.000 |
| alignment_precision | 1.000 |
| alignment_recall | 1.000 |

<details><summary>notes</summary>

0 unmatched span(s)
claim extraction LLM pass (replay): 15 of 15 passages answered by the model; the rest used the heuristic

</details>

## extraction[test]

| metric | value |
|---|---|
| passages | 16 |
| gold_spans | 38 |
| predicted_spans | 42 |
| alignment_rate | 1.000 |
| alignment_precision | 0.976 |
| alignment_recall | 1.000 |

<details><summary>notes</summary>

claim extraction LLM pass (replay): 16 of 16 passages answered by the model; the rest used the heuristic

</details>

---

Numbers are only comparable within the same environment block above. Changing the embedder changes them.
