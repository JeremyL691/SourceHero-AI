# Evaluation report

- **mode**: `offline`
- **suites**: `all`
- **generated**: 2026-10-07T00:13:04+00:00
- **git**: `4ffd81b`
- **python**: 3.12

## Environment

| setting | value |
|---|---|
| embedder | `hashing-v1` (dim 384) |
| embedder detail | `hashing-v1` |
| reranker | `none` |
| reranker detail | `rerank_provider is none` |
| llm provider | `fake` (mode `fake`) |

## retrieval

| metric | value |
|---|---|
| queries | 36 |
| recall@5 | 1.000 |
| mrr | 0.852 |
| ndcg@10 | 0.892 |
| latency_p50 | 3.309 |
| latency_p95 | 25.177 |

## claims

| metric | value |
|---|---|
| pairs | 80 |
| macro_f1 | 0.417 |
| accuracy | 0.487 |
| f1_contradicts | 0.820 |
| f1_duplicate | 0.400 |
| f1_supersedes | 0.400 |
| f1_supports | 0.321 |
| f1_unrelated | 0.143 |

<details><summary>notes</summary>

accuracy by phenomenon (n = cases):
  - date_moved: 0.00 (n=4)
  - duplicate: 1.00 (n=2)
  - negation: 0.86 (n=7)
  - paraphrase: 0.60 (n=10)
  - revised_schedule: 0.25 (n=4)
  - same_unit_value: 0.79 (n=24)
  - same_value: 0.00 (n=4)
  - trap_different_metric: 0.00 (n=8)
  - trap_qualifier_explains: 0.17 (n=6)
  - trap_same_number_diff_subject: 0.14 (n=7)
  - unit_conversion: 0.75 (n=4)
accuracy by language: en=0.49 (n=55), zh=0.48 (n=25)

</details>

## verify

| metric | value |
|---|---|
| cases | 66 |
| macro_f1 | 0.969 |
| accuracy | 0.970 |
| recall_contradicted | 0.938 |
| recall_disputed | 0.938 |
| recall_supported | 1.000 |
| recall_unsupported | 1.000 |
| latency_p50 | 0.248 |
| latency_p95 | 0.539 |

<details><summary>notes</summary>

accuracy by gold label: contradicted=0.94 (n=16), disputed=0.94 (n=16), supported=1.00 (n=17), unsupported=1.00 (n=17)

</details>

## extraction

| metric | value |
|---|---|
| passages | 15 |
| gold_spans | 61 |
| predicted_spans | 46 |
| alignment_rate | 1.000 |
| alignment_precision | 1.000 |
| alignment_recall | 0.754 |

<details><summary>notes</summary>

15 unmatched span(s)
  - [missed] It opened in 2014 after four years of construction.
  - [missed] The alloy resists corrosion in seawater.
  - [missed] The hospital opened its new wing in March.
  - [missed] Excavation finished in 2019.
  - [missed] Approval came in December.
  - [missed] Safety inspections passed last year.
  - [missed] Storage holds 1.1 gigawatt-hours.
  - [missed] 该数据中心于 2021 年建成。
  - [missed] 该中心运行 PUE 为 1.25。
  - [missed] 运维人员共 320 人。

</details>

## latency

| metric | value |
|---|---|
| retrieval_p50_ms | 3.405 |
| retrieval_p95_ms | 5.583 |
| verify_p50_ms | 0.264 |
| verify_p95_ms | 0.361 |
| retrieval_samples | 20 |
| verify_samples | 20 |

<details><summary>notes</summary>

retrieval: p50=3.4ms, p95=5.6ms (n=20)
verify: p50=0.3ms, p95=0.4ms (n=20)

</details>

## scifact

| metric | value |
|---|---|
| queries | 300 |
| corpus_size | 5183 |
| recall@5 | 0.367 |
| mrr | 0.305 |
| ndcg@10 | 0.331 |

<details><summary>notes</summary>

BEIR SciFact test split (300 queries against 5183 documents)
embedder: hashing-v1

</details>

---

Numbers are only comparable within the same environment block above. Changing the embedder changes them.
