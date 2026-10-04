# Evaluation report

- **mode**: `offline`
- **suites**: `all`
- **generated**: 2026-10-04T18:03:03+00:00
- **git**: `fd21209`
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
| ndcg@10 | 0.913 |
| latency_p50 | 2.759 |
| latency_p95 | 11.584 |

## claims

| metric | value |
|---|---|
| pairs | 80 |
| macro_f1 | 0.548 |
| accuracy | 0.450 |
| f1_contradicts | 0.679 |
| f1_duplicate | 0.500 |
| f1_supersedes | 0.400 |
| f1_supports | 0.562 |
| f1_unrelated | 0.600 |

<details><summary>notes</summary>

accuracy by phenomenon (n = cases):
  - date_moved: 0.00 (n=4)
  - duplicate: 1.00 (n=2)
  - negation: 1.00 (n=7)
  - paraphrase: 0.70 (n=10)
  - revised_schedule: 0.25 (n=4)
  - same_unit_value: 0.46 (n=24)
  - same_value: 0.00 (n=4)
  - trap_different_metric: 0.25 (n=8)
  - trap_qualifier_explains: 0.00 (n=6)
  - trap_same_number_diff_subject: 0.29 (n=7)
  - unit_conversion: 1.00 (n=4)
accuracy by language: en=0.45 (n=55), zh=0.44 (n=25)

</details>

## verify

| metric | value |
|---|---|
| cases | 66 |
| macro_f1 | 0.753 |
| accuracy | 0.742 |
| recall_contradicted | 0.500 |
| recall_disputed | 0.500 |
| recall_supported | 1.000 |
| recall_unsupported | 1.000 |
| latency_p50 | 0.035 |
| latency_p95 | 0.069 |

<details><summary>notes</summary>

accuracy by gold label: contradicted=0.50 (n=16), disputed=0.44 (n=16), supported=1.00 (n=17), unsupported=1.00 (n=17)

</details>

## extraction

| metric | value |
|---|---|
| passages | 15 |
| gold_spans | 61 |
| predicted_spans | 33 |
| alignment_rate | 1.000 |
| alignment_precision | 0.970 |
| alignment_recall | 0.525 |

<details><summary>notes</summary>

28 unmatched span(s)
  - [missed] The Verity trial enrolled 612 participants across nine 
  - [missed] The coastal rail line spans 44 kilo
  - [missed] etres. It opened in 2014 after four years of constru
  - [missed] tion. Daily ridership averages 31,000 pa
  - [missed] The alloy resists corrosion in seawater. 
  - [missed] atts. The addition supplied 480,0
  - [missed] 0 homes. Curtailment 
  - [missed] The hospital opened its new wing in Ma
  - [missed] ch. The wing holds 24
  - [missed]  beds. Staffing ros

</details>

---

Numbers are only comparable within the same environment block above. Changing the embedder changes them.
