# Evaluation report

- **mode**: `offline`
- **suites**: `claims,verify,extraction`
- **generated**: 2026-10-12T11:01:08+00:00
- **git**: `39abb46`
- **python**: 3.12

## Environment

| setting | value |
|---|---|
| embedder | `hashing-v1` (dim 384) |
| embedder detail | `hashing-v1` |
| reranker | `none` |
| reranker detail | `rerank_provider is none` |
| llm provider | `openrouter` (provider mode `cassette`) |
| eval llm mode | `replay` (model `nvidia/nemotron-3-super-120b-a12b:free`, requests 0, cassette `evals/cassettes/claims-dev.jsonl, evals/cassettes/claims-test.jsonl, evals/cassettes/verify-dev.jsonl, evals/cassettes/verify-test.jsonl, evals/cassettes/extraction-dev.jsonl, evals/cassettes/extraction-test.jsonl`) |
| split | `both` |
| dirty tree | `True` |

## claims

| metric | value |
|---|---|
| pairs | 80 |
| macro_f1 | 0.790 |
| accuracy | 0.875 |
| precision_contradicts | 0.969 |
| precision_duplicate | 0.875 |
| precision_refines | 1.000 |
| precision_supersedes | 1.000 |
| precision_supports | 0.650 |
| precision_unrelated | 0.938 |
| recall_contradicts | 0.969 |
| recall_duplicate | 1.000 |
| recall_refines | 0.200 |
| recall_supersedes | 0.750 |
| recall_supports | 0.929 |
| recall_unrelated | 0.833 |
| f1_contradicts | 0.969 |
| f1_duplicate | 0.933 |
| f1_refines | 0.333 |
| f1_supersedes | 0.857 |
| f1_supports | 0.765 |
| f1_unrelated | 0.882 |

<details><summary>notes</summary>

accuracy by phenomenon (n = cases):
  - date_moved: 1.00 (n=4)
  - duplicate: 1.00 (n=3)
  - negation: 1.00 (n=8)
  - paraphrase: 1.00 (n=10)
  - qualifier_refines: 0.00 (n=1)
  - revised_schedule: 0.75 (n=4)
  - same_unit_value: 0.95 (n=20)
  - same_value: 1.00 (n=4)
  - trap_different_metric: 0.75 (n=8)
  - trap_qualifier_explains: 0.43 (n=7)
  - trap_same_number_diff_subject: 1.00 (n=7)
  - unit_conversion: 0.75 (n=4)
accuracy by language: en=0.81 (n=53), zh=1.00 (n=27)

</details>

## claims[test]

| metric | value |
|---|---|
| pairs | 156 |
| macro_f1 | 0.673 |
| accuracy | 0.705 |
| precision_contradicts | 0.714 |
| precision_duplicate | 0.846 |
| precision_refines | 1.000 |
| precision_supersedes | 1.000 |
| precision_supports | 0.424 |
| precision_unrelated | 1.000 |
| recall_contradicts | 0.641 |
| recall_duplicate | 0.846 |
| recall_refines | 0.154 |
| recall_supersedes | 0.692 |
| recall_supports | 0.962 |
| recall_unrelated | 0.731 |
| f1_contradicts | 0.676 |
| f1_duplicate | 0.846 |
| f1_refines | 0.267 |
| f1_supersedes | 0.818 |
| f1_supports | 0.588 |
| f1_unrelated | 0.844 |

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
| latency_p50 | 0.240 |
| latency_p95 | 0.508 |

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
| latency_p50 | 0.263 |
| latency_p95 | 0.501 |

<details><summary>notes</summary>

verify LLM pass (replay): 27 of 44 sentences were undecided or contested; 25 judged by the model, 2 kept the heuristic verdict (0 unanswered)

</details>

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
