"""Wayback Machine backfill.

The point is not archival completeness -- it is making the *temporal* model testable
and correct. A snapshot inserted with an `effective_at` older than the live version is
exactly the case where "current" must follow world time rather than insertion order, and
where a document's claim lifecycle has to be recomputed from scratch.

Only the CDX index and the snapshot bytes are fetched; everything else is the ordinary
versioning path.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.core.logging import get_logger
from app.ingestion.fetch import fetch
from app.ingestion.webpage import extract_webpage

log = get_logger(__name__)

CDX_URL = "https://web.archive.org/cdx/search/cdx"
SNAPSHOT_TEMPLATE = "https://web.archive.org/web/{timestamp}id_/{original}"
MAX_WINDOW_DAYS = 90


@dataclass(frozen=True)
class Snapshot:
    timestamp: str
    original: str
    statuscode: str
    mimetype: str = ""

    @property
    def archived_at(self) -> datetime:
        return datetime.strptime(self.timestamp, "%Y%m%d%H%M%S").replace(tzinfo=UTC)

    def url(self) -> str:
        # `id_` asks for the original bytes rather than the wayback toolbar rewrite.
        return SNAPSHOT_TEMPLATE.format(timestamp=self.timestamp, original=self.original)


def _parse_cdx(rows: list[list[str]], limit: int) -> list[Snapshot]:
    """Turn a CDX response into an evenly spread selection of snapshots.

    Always keeping the first and last: the oldest and newest are the two that define
    a document's trajectory, and a naive "evenly spaced, take N" can drop both.
    """
    if not rows:
        return []
    header, *body = rows
    try:
        timestamp_at = header.index("timestamp")
        original_at = header.index("original")
        status_at = header.index("statuscode")
        mime_at = header.index("mimetype") if "mimetype" in header else None
    except ValueError as exc:
        raise ValueError(f"unexpected CDX columns: {header}") from exc

    snapshots = [
        Snapshot(
            timestamp=row[timestamp_at],
            original=row[original_at],
            statuscode=row[status_at],
            mimetype=row[mime_at] if mime_at is not None and len(row) > mime_at else "",
        )
        for row in body
        if len(row) > max(timestamp_at, original_at, status_at)
    ]
    if len(snapshots) <= limit:
        return snapshots
    if limit == 1:
        return [snapshots[-1]]
    step = (len(snapshots) - 1) / (limit - 1)
    picks = [snapshots[int(round(i * step))] for i in range(limit)]
    # Rounding can collide on the same index; de-duplicate while keeping order.
    seen: set[str] = set()
    out: list[Snapshot] = []
    for snapshot in picks:
        if snapshot.timestamp not in seen:
            seen.add(snapshot.timestamp)
            out.append(snapshot)
    return out


def list_snapshots(
    url: str, *, limit: int = 8, from_: str | None = None, to: str | None = None
) -> list[Snapshot]:
    """Snapshots of `url`, oldest first."""
    params = {
        "url": url,
        "output": "json",
        "fl": "timestamp,original,statuscode,digest,mimetype",
        "filter": "statuscode:200",
        "collapse": "digest",
    }
    if from_:
        params["from"] = from_
    if to:
        params["to"] = to

    from app.ingestion.fetch import get_client
    from app.ingestion.netguard import validate_url

    validate_url(url)
    response = get_client().get(CDX_URL, params=params, timeout=30.0)
    response.raise_for_status()
    return _parse_cdx(response.json(), limit)


def wayback_backfill(ctx, payload: dict) -> dict:
    """Fetch a source's snapshots into versions, then queue reconcile."""
    from sqlalchemy import select

    from app.ingestion.versions import upsert_document_version
    from app.models.document import Document
    from app.models.source import Source

    db = ctx.db
    source_id = payload.get("source_id")
    document_id = payload.get("document_id")
    if not source_id and not document_id:
        return {"skipped": "need source_id or document_id"}

    source = db.get(Source, uuid.UUID(str(source_id))) if source_id else None
    documents: list[Document] = []
    if document_id:
        document = db.get(Document, uuid.UUID(str(document_id)))
        if document is None:
            return {"skipped": f"document {document_id} not found"}
        documents = [document]
        source = db.get(Source, document.source_id)
    elif source is not None:
        documents = list(db.execute(select(Document).where(Document.source_id == source.id)).scalars())
    if source is None:
        return {"skipped": "source not found"}
    if source.source_type not in ("webpage", "rss"):
        return {"skipped": f"backfill only applies to webpage/rss sources, not {source.source_type!r}"}

    limit = int(payload.get("limit", 8))
    stats = {"versions": 0, "documents": 0, "snapshots": 0, "errors": 0}
    for document in documents:
        if not document.url:
            continue
        try:
            snapshots = list_snapshots(
                document.url,
                limit=limit,
                from_=payload.get("from"),
                to=payload.get("to"),
            )
        except Exception as exc:  # noqa: BLE001 - one document's history failing is not fatal
            log.warning("wayback: could not list snapshots for %s: %s", document.url, exc)
            stats["errors"] += 1
            continue

        for snapshot in snapshots:
            try:
                page = fetch(snapshot.url())
            except Exception as exc:  # noqa: BLE001
                log.warning("wayback: snapshot %s unavailable: %s", snapshot.timestamp, exc)
                stats["errors"] += 1
                continue
            stats["snapshots"] += 1
            orig_url = document.url or source.url
            extracted = extract_webpage(
                page.content.decode("utf-8", errors="replace"),
                orig_url or snapshot.url(),
            )
            if orig_url:
                extracted.url = orig_url
            result = upsert_document_version(
                db,
                source=source,
                extracted=extracted,
                origin="wayback",
                effective_at=snapshot.archived_at,
                archived_at=snapshot.archived_at,
                fetched_at=page.fetched_at,
            )
            if result.is_new_or_changed and result.version is not None:
                stats["versions"] += 1
                ctx.enqueue("index_version", {"version_id": result.version.id})
        stats["documents"] += 1

        if stats["versions"]:
            # Debounced: a backfill inserting several snapshots should reconcile once.
            ctx.enqueue(
                "reconcile_document",
                {"document_id": str(document.id)},
                dedupe_key=f"reconcile:{document.id}",
            )

    return stats


def clamp_window(start: datetime | None, end: datetime | None) -> tuple[datetime | None, datetime | None]:
    """A what-changed query over years is not a digest, it is a backlog."""
    if start is None or end is None:
        return start, end
    if (end - start).days <= MAX_WINDOW_DAYS:
        return start, end
    log.info("clamping belief-diff window to %d days", MAX_WINDOW_DAYS)
    return end - timedelta(days=MAX_WINDOW_DAYS), end


__all__ = ["CDX_URL", "MAX_WINDOW_DAYS", "Snapshot", "clamp_window", "list_snapshots", "wayback_backfill"]
