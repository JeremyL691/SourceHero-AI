# Evaluation report

- **mode**: `full`
- **suites**: `retrieval`
- **generated**: 2026-10-12T19:41:34+00:00
- **git**: `e4edbfd`
- **python**: 3.12

## Environment

| setting | value |
|---|---|
| embedder | `fastembed:sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` (dim 384) |
| embedder detail | `fastembed:sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` |
| reranker | `Xenova/ms-marco-MiniLM-L-6-v2` |
| reranker detail | `rerank` |
| llm provider | `fake` (provider mode `fake`) |
| eval llm mode | `none` (model `none`, requests 0, cassette `none`) |
| split | `dev` |
| dirty tree | `True` |

## retrieval

| metric | value |
|---|---|
| queries | 36 |
| recall@5 | 1.000 |
| mrr | 0.931 |
| ndcg@10 | 0.947 |
| latency_p50 | 113.552 |
| latency_p95 | 288.554 |

---

Numbers are only comparable within the same environment block above. Changing the embedder changes them.
