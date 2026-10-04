"""Re-export of the ingestion handlers, so `register_all` has one module per domain."""

from __future__ import annotations

from app.ingestion.pipeline import (  # noqa: F401
    extract_claims_job,
    index_version_job,
    ingest_source_job,
    reindex_embeddings_job,
)

__all__ = [
    "extract_claims_job",
    "index_version_job",
    "ingest_source_job",
    "reindex_embeddings_job",
]
