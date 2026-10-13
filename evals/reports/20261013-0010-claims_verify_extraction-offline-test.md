# Evaluation report

- **mode**: `offline`
- **suites**: `claims,verify,extraction`
- **generated**: 2026-10-13T00:10:10+00:00
- **git**: `6cde4da`
- **python**: 3.12

## Environment

| setting | value |
|---|---|
| embedder | `hashing-v1` (dim 384) |
| embedder detail | `hashing-v1` |
| reranker | `none` |
| reranker detail | `rerank_provider is none` |
| llm provider | `fake` (provider mode `fake`) |
| eval llm mode | `none` (model `none`, requests 0, cassette `none`) |
| split | `test` |
| dirty tree | `False` |

## claims[test]

| metric | value |
|---|---|
| pairs | 156 |
| macro_f1 | 0.527 |
| accuracy | 0.609 |
| precision_contradicts | 0.706 |
| precision_duplicate | 0.846 |
| precision_refines | 0.000 |
| precision_supersedes | 1.000 |
| precision_supports | 0.346 |
| precision_unrelated | 0.717 |
| recall_contradicts | 0.615 |
| recall_duplicate | 0.846 |
| recall_refines | 0.000 |
| recall_supersedes | 0.308 |
| recall_supports | 0.692 |
| recall_unrelated | 0.731 |
| f1_contradicts | 0.658 |
| f1_duplicate | 0.846 |
| f1_refines | 0.000 |
| f1_supersedes | 0.471 |
| f1_supports | 0.462 |
| f1_unrelated | 0.724 |

## verify[test]

| metric | value |
|---|---|
| cases | 44 |
| macro_f1 | 0.734 |
| accuracy | 0.727 |
| precision_contradicted | 1.000 |
| precision_disputed | 1.000 |
| precision_supported | 0.471 |
| precision_unsupported | 0.786 |
| recall_contradicted | 0.636 |
| recall_disputed | 0.545 |
| recall_supported | 0.727 |
| recall_unsupported | 1.000 |
| f1_contradicted | 0.778 |
| f1_disputed | 0.706 |
| f1_supported | 0.571 |
| f1_unsupported | 0.880 |
| latency_p50 | 0.302 |
| latency_p95 | 0.563 |

## extraction[test]

| metric | value |
|---|---|
| passages | 16 |
| gold_spans | 38 |
| predicted_spans | 30 |
| alignment_rate | 1.000 |
| alignment_precision | 1.000 |
| alignment_recall | 0.789 |

---

Numbers are only comparable within the same environment block above. Changing the embedder changes them.
