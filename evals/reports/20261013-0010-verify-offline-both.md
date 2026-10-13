# Evaluation report

- **mode**: `offline`
- **suites**: `verify`
- **generated**: 2026-10-13T00:10:12+00:00
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
| eval llm mode | `replay` (model `nvidia/nemotron-3-super-120b-a12b:free`, requests 0, cassette `evals/cassettes/verify-dev.jsonl, evals/cassettes/verify-test.jsonl`) |
| split | `both` |
| dirty tree | `False` |

## verify

| metric | value |
|---|---|
| cases | 66 |
| macro_f1 | 0.969 |
| accuracy | 0.970 |
| precision_contradicted | 1.000 |
| precision_disputed | 1.000 |
| precision_supported | 0.944 |
| precision_unsupported | 0.944 |
| recall_contradicted | 1.000 |
| recall_disputed | 0.875 |
| recall_supported | 1.000 |
| recall_unsupported | 1.000 |
| f1_contradicted | 1.000 |
| f1_disputed | 0.933 |
| f1_supported | 0.971 |
| f1_unsupported | 0.971 |
| latency_p50 | 0.261 |
| latency_p95 | 0.561 |

<details><summary>notes</summary>

accuracy by gold label: contradicted=1.00 (n=16), disputed=0.88 (n=16), supported=1.00 (n=17), unsupported=1.00 (n=17)
verify LLM pass (replay): 48 of 66 sentences were undecided or contested; 44 judged by the model, 4 kept the heuristic verdict (0 unanswered)

</details>

## verify[test]

| metric | value |
|---|---|
| cases | 44 |
| macro_f1 | 0.767 |
| accuracy | 0.773 |
| precision_contradicted | 1.000 |
| precision_disputed | 1.000 |
| precision_supported | 0.550 |
| precision_unsupported | 0.917 |
| recall_contradicted | 0.636 |
| recall_disputed | 0.455 |
| recall_supported | 1.000 |
| recall_unsupported | 1.000 |
| f1_contradicted | 0.778 |
| f1_disputed | 0.625 |
| f1_supported | 0.710 |
| f1_unsupported | 0.957 |
| latency_p50 | 0.277 |
| latency_p95 | 0.535 |

<details><summary>notes</summary>

verify LLM pass (replay): 27 of 44 sentences were undecided or contested; 25 judged by the model, 2 kept the heuristic verdict (0 unanswered)

</details>

---

Numbers are only comparable within the same environment block above. Changing the embedder changes them.
