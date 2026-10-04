"""Source and document endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, Query, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, Pagination, get_db, require_scope
from app.core import time as clock
from app.core.errors import DomainValidationError
from app.ingestion.sources import (
    create_source,
    delete_source,
    get_source,
    list_sources,
    store_upload,
    update_source,
)
from app.jobs import queue as q
from app.models.chunk import Chunk, IngestionRun
from app.models.document import Document, DocumentVersion
from app.models.source import Source
from app.schemas.documents import (
    DocumentDetailOut,
    DocumentOut,
    IngestionRunOut,
    SourceCreateIn,
    SourceOut,
    SourceUpdateIn,
    VersionOut,
)

router = APIRouter(tags=["sources"])


@router.get("/sources", response_model=list[SourceOut], summary="List sources")
def get_sources(
    source_type: str | None = None,
    user: CurrentUser = Depends(require_scope("read")),
    db: Session = Depends(get_db),
) -> list[SourceOut]:
    rows = list_sources(db, user.id, source_type=source_type)
    counts = dict(
        db.execute(
            select(Document.source_id, func.count(Document.id))
            .join(Source, Source.id == Document.source_id)
            .where(Source.user_id == user.id)
            .group_by(Document.source_id)
        ).all()
    )
    out = []
    for source in rows:
        out.append(
            SourceOut.model_validate(source).model_copy(update={"document_count": counts.get(source.id, 0)})
        )
    return out


@router.post(
    "/sources",
    response_model=SourceOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a source",
)
def post_source(
    payload: SourceCreateIn,
    user: CurrentUser = Depends(require_scope("write")),
    db: Session = Depends(get_db),
) -> SourceOut:
    source = create_source(
        db,
        user.id,
        source_type=payload.source_type,
        name=payload.name,
        url=payload.url,
        config=payload.config,
        watch=payload.watch,
        refresh_interval_minutes=payload.refresh_interval_minutes,
    )
    return SourceOut.model_validate(source)


@router.get("/sources/{source_id}", response_model=SourceOut, summary="Get one source")
def get_one_source(
    source_id: uuid.UUID,
    user: CurrentUser = Depends(require_scope("read")),
    db: Session = Depends(get_db),
) -> SourceOut:
    return SourceOut.model_validate(get_source(db, user.id, source_id))


@router.patch("/sources/{source_id}", response_model=SourceOut, summary="Update a source")
def patch_source(
    source_id: uuid.UUID,
    payload: SourceUpdateIn,
    user: CurrentUser = Depends(require_scope("write")),
    db: Session = Depends(get_db),
) -> SourceOut:
    source = update_source(db, user.id, source_id, **payload.model_dump(exclude_unset=True))
    return SourceOut.model_validate(source)


@router.delete("/sources/{source_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a source")
def delete_one_source(
    source_id: uuid.UUID,
    user: CurrentUser = Depends(require_scope("write")),
    db: Session = Depends(get_db),
) -> None:
    delete_source(db, user.id, source_id)


@router.post(
    "/sources/{source_id}/refresh",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Queue an ingest of a source",
)
def refresh_source(
    source_id: uuid.UUID,
    user: CurrentUser = Depends(require_scope("write")),
    db: Session = Depends(get_db),
) -> dict:
    source = get_source(db, user.id, source_id)
    job = q.enqueue(
        db,
        "ingest_source",
        {"source_id": str(source.id)},
        user_id=user.id,
        dedupe_key=f"ingest:{source.id}",
        now=clock.utcnow(),
    )
    db.commit()
    return {"job_id": job.id}


@router.post(
    "/sources/{source_id}/wayback",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Queue a Wayback backfill for a source",
)
def backfill_source(
    source_id: uuid.UUID,
    limit: int = Query(8, ge=1, le=50),
    from_: str | None = Query(None, alias="from"),
    to: str | None = None,
    user: CurrentUser = Depends(require_scope("write")),
    db: Session = Depends(get_db),
) -> dict:
    source = get_source(db, user.id, source_id)
    job = q.enqueue(
        db,
        "wayback_backfill",
        {"source_id": str(source.id), "limit": limit, "from": from_, "to": to},
        user_id=user.id,
        dedupe_key=f"wayback:{source.id}",
        now=clock.utcnow(),
    )
    db.commit()
    return {"job_id": job.id}


@router.post(
    "/sources/upload",
    response_model=SourceOut,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a pdf/md/txt/html file",
)
def upload_source(
    file: UploadFile = File(...),
    name: str | None = None,
    user: CurrentUser = Depends(require_scope("write")),
    db: Session = Depends(get_db),
) -> SourceOut:
    data = file.file.read()
    key, source_type = store_upload(user.id, file.filename or "", data)
    source = create_source(
        db,
        user.id,
        source_type=source_type,
        name=name or (file.filename or "upload"),
        url=None,
    )
    source.storage_key = key
    db.commit()
    db.refresh(source)
    job = q.enqueue(
        db,
        "ingest_source",
        {"source_id": str(source.id)},
        user_id=user.id,
        dedupe_key=f"ingest:{source.id}",
        now=clock.utcnow(),
    )
    db.commit()
    _ = job
    return SourceOut.model_validate(source)


@router.get("/ingestion-runs", response_model=list[IngestionRunOut], summary="Ingest history")
def get_ingestion_runs(
    source_id: uuid.UUID | None = None,
    page: Pagination = Depends(),
    user: CurrentUser = Depends(require_scope("read")),
    db: Session = Depends(get_db),
) -> list[IngestionRunOut]:
    stmt = select(IngestionRun).where(IngestionRun.user_id == user.id)
    if source_id:
        stmt = stmt.where(IngestionRun.source_id == source_id)
    rows = db.execute(stmt.order_by(IngestionRun.id.desc()).limit(page.limit).offset(page.offset))
    return [IngestionRunOut.model_validate(r) for r in rows.scalars()]


# --- documents -----------------------------------------------------------


@router.get("/documents", response_model=list[DocumentOut], summary="List documents")
def get_documents(
    source_id: uuid.UUID | None = None,
    page: Pagination = Depends(),
    user: CurrentUser = Depends(require_scope("read")),
    db: Session = Depends(get_db),
) -> list[DocumentOut]:
    stmt = select(Document).where(Document.user_id == user.id)
    if source_id:
        stmt = stmt.where(Document.source_id == source_id)
    rows = db.execute(
        stmt.order_by(Document.last_seen_at.desc().nullslast()).limit(page.limit).offset(page.offset)
    ).scalars()
    return [DocumentOut.model_validate(d) for d in rows]


@router.get("/documents/{document_id}", response_model=DocumentDetailOut, summary="Get one document")
def get_document(
    document_id: uuid.UUID,
    user: CurrentUser = Depends(require_scope("read")),
    db: Session = Depends(get_db),
) -> DocumentDetailOut:
    document = _document(db, user.id, document_id)
    from app.ingestion.versions import current_text

    chunk_count = db.execute(
        select(func.count()).select_from(Chunk).where(Chunk.version_id == document.current_version_id)
    ).scalar_one()
    return DocumentDetailOut.model_validate(document).model_copy(
        update={
            "text": current_text(db, document.id),
            "chunk_count": chunk_count,
            "version_count": db.execute(
                select(func.count())
                .select_from(DocumentVersion)
                .where(DocumentVersion.document_id == document.id)
            ).scalar_one(),
        }
    )


@router.get("/documents/{document_id}/versions", response_model=list[VersionOut], summary="Version history")
def get_document_versions(
    document_id: uuid.UUID,
    user: CurrentUser = Depends(require_scope("read")),
    db: Session = Depends(get_db),
) -> list[VersionOut]:
    _document(db, user.id, document_id)
    rows = db.execute(
        select(DocumentVersion)
        .where(DocumentVersion.document_id == document_id)
        .order_by(DocumentVersion.effective_at)
    ).scalars()
    return [VersionOut.model_validate(v) for v in rows]


@router.get(
    "/documents/{document_id}/versions/{version_id}",
    response_model=VersionOut,
    summary="One version, with its text",
)
def get_document_version(
    document_id: uuid.UUID,
    version_id: int,
    user: CurrentUser = Depends(require_scope("read")),
    db: Session = Depends(get_db),
) -> VersionOut:
    _document(db, user.id, document_id)
    version = db.get(DocumentVersion, version_id)
    if version is None or version.document_id != document_id:
        from app.core.errors import NotFoundError

        raise NotFoundError("version not found")
    return VersionOut.model_validate(version)


@router.get("/documents/{document_id}/diff", summary="Diff two versions of a document")
def diff_document(
    document_id: uuid.UUID,
    from_id: int = Query(..., alias="from"),
    to_id: int = Query(..., alias="to"),
    user: CurrentUser = Depends(require_scope("read")),
    db: Session = Depends(get_db),
) -> dict:
    import difflib

    _document(db, user.id, document_id)
    before = db.get(DocumentVersion, from_id)
    after = db.get(DocumentVersion, to_id)
    if before is None or after is None or before.document_id != document_id:
        from app.core.errors import NotFoundError

        raise NotFoundError("version not found")
    diff = "\n".join(
        difflib.unified_diff(
            before.clean_text.splitlines(),
            after.clean_text.splitlines(),
            fromfile=f"v{before.version_no}",
            tofile=f"v{after.version_no}",
            lineterm="",
            n=2,
        )
    )
    return {"from": before.version_no, "to": after.version_no, "diff": diff}


def _document(db: Session, user_id: uuid.UUID, document_id: uuid.UUID) -> Document:
    document = db.get(Document, document_id)
    if document is None or document.user_id != user_id:
        from app.core.errors import NotFoundError

        raise NotFoundError("document not found")
    return document


__all__ = ["DomainValidationError", "router"]
