# Evaluation report

- **mode**: `full`
- **suites**: `retrieval,scifact`
- **generated**: 2026-10-13T00:10:06+00:00
- **git**: `6cde4da`
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
| dirty tree | `False` |

## retrieval

| metric | value |
|---|---|
| queries | 36 |
| recall@5 | 1.000 |
| mrr | 0.931 |
| ndcg@10 | 0.947 |
| latency_p50 | 119.053 |
| latency_p95 | 367.953 |

## scifact

artifact: `evals/reports/20261013-0010-scifact-fastembed-sentence-transformers-paraphrase-multilingual-minilm-l12-v2.md`

| metric | value |
|---|---|
| queries | 300 |
| corpus_size | 5183 |
| recall@5 | 0.680 |
| mrr | 0.606 |
| ndcg@10 | 0.627 |

<details><summary>notes</summary>

BEIR SciFact test split (300 queries against 5183 documents)
embedder: fastembed:sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2

</details>

---

Numbers are only comparable within the same environment block above. Changing the embedder changes them.
