# Evaluation report

- **mode**: `offline`
- **suites**: `claims`
- **generated**: 2026-10-13T00:10:11+00:00
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
| eval llm mode | `replay` (model `nvidia/nemotron-3-super-120b-a12b:free`, requests 0, cassette `evals/cassettes/claims-dev.jsonl, evals/cassettes/claims-test.jsonl`) |
| split | `both` |
| dirty tree | `False` |

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

---

Numbers are only comparable within the same environment block above. Changing the embedder changes them.
