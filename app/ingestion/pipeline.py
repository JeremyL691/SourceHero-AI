"""The ingestion pipeline and its job handlers.

Chain: `ingest_source` (fetch + extract + version) -> `index_version` (chunk +
embed) -> `extract_claims` (a placeholder until M8 replaces it).

Handlers are idempotent. Re-running one must not create duplicate rows, which is why
every write is keyed by a natural unique constraint rather than by "has this run
happened before".
"""

from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import time as clock
from app.core.logging import get_logger
from app.core.security import hash_password
from app.ingestion.fetch import FetchError, fetch, sanitize_error
from app.ingestion.pdf import extract_pdf, extract_text_file
from app.ingestion.rss import extract_feed
from app.ingestion.types import ExtractedDocument
from app.ingestion.versions import upsert_document_version
from app.ingestion.webpage import extract_webpage
from app.jobs.registry import JobContext, NonRetryableJobError, job_handler
from app.models.chunk import IngestionRun
from app.models.source import Source

log = get_logger(__name__)

WATCH_DEFAULT_MINUTES = 1440


def _extract_for_source(source: Source) -> list[ExtractedDocument]:
    """Dispatch on `source_type`. Raises `FetchError` for anything network-bound."""
    kind = source.source_type
    if kind in ("webpage", "clip"):
        if not source.url:
            raise FetchError("unsupported_scheme", "webpage source has no url")
        page = fetch(source.url)
        html = page.content.decode("utf-8", errors="replace")
        return [extract_webpage(html, page.final_url)]
    if kind == "rss":
        if not source.url:
            raise FetchError("unsupported_scheme", "rss source has no url")
        return extract_feed(source.url)
    if kind == "pdf":
        if not source.storage_key and not source.local_path:
            raise FetchError("unsupported_scheme", "pdf source has no stored file")
        data = _read_stored(source)
        return [extract_pdf(data, url=source.url, title=source.name)]
    if kind in ("file", "note", "conversation", "demo"):
        return [
            extract_text_file(
                _read_stored(source),
                filename=source.storage_key or source.local_path or "",
                url=source.url,
                title=source.name,
            )
        ]
    raise FetchError("unsupported_scheme", f"cannot ingest source_type {kind!r}")


def _read_stored(source: Source) -> bytes:
    from pathlib import Path

    if source.local_path:
        return Path(source.local_path).read_bytes()
    if source.storage_key:
        from app.storage import get_storage

        return get_storage().download(source.storage_key)
    raise FetchError("unsupported_scheme", f"source {source.id} has no readable content")


@job_handler("ingest_source")
@job_handler("refresh_source")
def ingest_source_job(ctx: JobContext, payload: dict) -> dict:
    """Fetch a source, store its documents as versions, and queue indexing."""
    db = ctx.db
    source_id = payload.get("source_id")
    if not source_id:
        return {"skipped": "no source_id in payload"}

    source = db.get(Source, uuid.UUID(str(source_id)))
    if source is None:
        return {"skipped": f"source {source_id} not found"}
    if source.status == "paused":
        # Non-retryable: a paused source is a deliberate state, not a transient error.
        raise NonRetryableJobError(f"source {source.id} is paused")

    run = IngestionRun(user_id=source.user_id, source_id=source.id, job_id=ctx.job.id)
    db.add(run)
    db.flush()

    stats = {
        "documents_found": 0,
        "documents_new": 0,
        "documents_changed": 0,
        "documents_unchanged": 0,
    }
    warnings: list[str] = []
    indexed: list[int] = []

    try:
        documents = _extract_for_source(source)
    except FetchError as exc:
        run.status = "failed"
        run.error = sanitize_error(exc)
        run.warnings = [{"code": exc.code}]
        run.finished_at = clock.utcnow()
        source.status = "failed"
        db.flush()
        raise NonRetryableJobError(run.error) from exc
    except Exception as exc:  # noqa: BLE001 - record, then let the queue retry
        run.status = "failed"
        run.error = sanitize_error(exc)
        run.finished_at = clock.utcnow()
        db.flush()
        raise

    stats["documents_found"] = len(documents)
    for extracted in documents:
        for warning in extracted.warnings:
            warnings.append({"document": extracted.url or extracted.title, "warning": warning})
        result = upsert_document_version(db, source=source, extracted=extracted)
        stats[f"documents_{result.status}"] = stats.get(f"documents_{result.status}", 0) + 1
        if result.is_new_or_changed and result.version is not None:
            indexed.append(result.version.id)
            ctx.enqueue("index_version", {"version_id": result.version.id}, now=clock.utcnow())

    run.status = "succeeded"
    run.warnings = warnings
    run.finished_at = clock.utcnow()
    source.last_ingested_at = clock.utcnow()
    source.status = "active"
    if source.watch:
        interval = source.refresh_interval_minutes or WATCH_DEFAULT_MINUTES
        source.next_refresh_at = clock.utcnow() + timedelta(minutes=interval)
    db.flush()

    return {**stats, "indexed_versions": indexed, "warnings": len(warnings)}


@job_handler("index_version")
def index_version_job(ctx: JobContext, payload: dict) -> dict:
    """Chunk and embed one version, then queue claim extraction."""
    from app.indexing.indexer import index_version

    version_id = payload.get("version_id")
    if version_id is None:
        return {"skipped": "no version_id in payload"}

    result = index_version(ctx.db, int(version_id), force=bool(payload.get("force")))
    ctx.enqueue("extract_claims", {"version_id": int(version_id)}, now=clock.utcnow())
    return result


@job_handler("extract_claims")
def extract_claims_job(ctx: JobContext, payload: dict) -> dict:
    """Extract claims from a version, cluster them, then judge relations.

    Clustering and relation judging run under an advisory lock: two workers must not
    each see a different half of the uncommitted claims and conclude that two claims
    from the same ingest have nothing to do with each other.
    """
    from app.claims.cluster import assign_clusters, cluster_label
    from app.claims.extract import extract_version_claims
    from app.claims.relate import judge_relations
    from app.models.claim import Claim
    from app.models.document import DocumentVersion
    from app.services.advisory import session_lock

    version_id = payload.get("version_id")
    if version_id is None:
        return {"skipped": "no version_id in payload"}

    version = ctx.db.get(DocumentVersion, int(version_id))
    if version is None:
        return {"skipped": f"version {version_id} not found"}

    stats = extract_version_claims(ctx.db, int(version_id))
    ctx.progress({"claims": stats.created})

    if not stats.created:
        return {**stats.as_dict(), "clusters": 0, "relations": {}}

    with session_lock(ctx.db, f"claims:{ctx.user_id}"):
        claims = [ctx.db.get(Claim, claim_id) for claim_id in stats.ids]
        claims = [c for c in claims if c is not None]
        assign_clusters(ctx.db, claims)
        for claim in claims:
            from app.models.claim import ClaimCluster

            cluster = ctx.db.get(ClaimCluster, claim.cluster_id) if claim.cluster_id else None
            if cluster is not None and not cluster.label:
                cluster.label = cluster_label(ctx.db, cluster)
        relations = judge_relations(ctx.db, stats.ids)
        # Judging can tie two clusters together; relabel the survivor.
        for claim in claims:
            from app.models.claim import ClaimCluster

            cluster = ctx.db.get(ClaimCluster, claim.cluster_id) if claim.cluster_id else None
            if cluster is not None and not cluster.label:
                cluster.label = cluster_label(ctx.db, cluster)

    ctx.enqueue(
        "plan_topics",
        {"user_id": str(ctx.user_id)},
        dedupe_key=f"plan_topics:{ctx.user_id}",
        now=clock.utcnow(),
    )
    return {**stats.as_dict(), "relations": relations}


@job_handler("reindex_embeddings")
def reindex_embeddings_job(ctx: JobContext, payload: dict) -> dict:
    from app.indexing.indexer import reindex_embeddings

    return reindex_embeddings(ctx.db, ctx.user_id)


def ensure_user(db: Session, email: str, password: str, *, display_name: str | None = None):
    """Create the user if absent. Used by the demo seed and the test harness."""
    from app.models.user import User

    normalized = email.strip().lower()
    existing = db.execute(select(User).where(User.email == normalized)).scalar_one_or_none()
    if existing is not None:
        return existing, False
    user = User(
        email=normalized,
        password_hash=hash_password(password),
        display_name=display_name or normalized.split("@")[0],
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user, True


__all__ = [
    "ensure_user",
    "extract_claims_job",
    "index_version_job",
    "ingest_source_job",
    "reindex_embeddings_job",
]
