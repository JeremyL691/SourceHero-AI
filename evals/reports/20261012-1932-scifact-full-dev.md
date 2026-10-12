# Evaluation report

- **mode**: `full`
- **suites**: `scifact`
- **generated**: 2026-10-12T19:32:42+00:00
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

## scifact

artifact: `evals/reports/20261012-1932-scifact-fastembed-sentence-transformers-paraphrase-multilingual-minilm-l12-v2.md`

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
