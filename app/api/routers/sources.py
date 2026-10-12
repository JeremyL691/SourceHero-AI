"""Source and document endpoints."""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.orm import InstrumentedAttribute, Session
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile as FormUploadFile

from app.api.deps import CurrentUser, Pagination, get_db, require_scope
from app.core import time as clock
from app.core.errors import DomainValidationError, PayloadTooLargeError
from app.ingestion.sources import (
    MAX_UPLOAD_BYTES,
    UPLOAD_EXTENSIONS,
    create_source,
    delete_source,
    get_source,
    list_sources,
    store_upload,
    update_source,
)
from app.jobs import queue as q
from app.library.service import document_refs
from app.models.chunk import Chunk, IngestionRun
from app.models.document import Document, DocumentVersion
from app.models.library import Collection, CollectionItem, DocumentFlag, ItemTag, Tag
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
    page: Pagination = Depends(),
    user: CurrentUser = Depends(require_scope("read")),
    db: Session = Depends(get_db),
) -> list[SourceOut]:
    rows = list_sources(db, user.id, source_type=source_type, limit=page.limit, offset=page.offset)
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
        dedupe_key=f"wayback:{source.id}:{from_ or ''}:{to or ''}:{limit}",
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
    chunk_size = 1024 * 1024
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = file.file.read(chunk_size)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_UPLOAD_BYTES:
            raise PayloadTooLargeError(
                f"file is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)}MB",
                code="file_too_large",
            )
        chunks.append(chunk)
    data = b"".join(chunks)
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


def _flag_filter(user_id: uuid.UUID, column: InstrumentedAttribute[bool], wanted: bool):
    flagged = select(DocumentFlag.document_id).where(DocumentFlag.user_id == user_id, column.is_(True))
    return Document.id.in_(flagged) if wanted else Document.id.not_in(flagged)


@router.get("/documents", response_model=list[DocumentOut], summary="List documents")
def get_documents(
    source_id: uuid.UUID | None = None,
    source_type: str | None = None,
    tag_id: int | None = None,
    collection_id: int | None = None,
    starred: bool | None = None,
    archived: bool | None = None,
    search: str | None = Query(None, alias="q", description="Case-insensitive match on title or URL"),
    page: Pagination = Depends(),
    user: CurrentUser = Depends(require_scope("read")),
    db: Session = Depends(get_db),
) -> list[DocumentOut]:
    stmt = (
        select(Document, Source.source_type)
        .join(Source, Source.id == Document.source_id)
        .where(Document.user_id == user.id)
    )
    if source_id:
        stmt = stmt.where(Document.source_id == source_id)
    if source_type:
        stmt = stmt.where(Source.source_type == source_type)
    # Same shape as hybrid.py: a document matches when its id is in a per-user subquery.
    # A tag or collection owned by someone else matches nothing, so the list is empty.
    if tag_id is not None:
        tagged = (
            select(ItemTag.document_id)
            .join(Tag, Tag.id == ItemTag.tag_id)
            .where(ItemTag.tag_id == tag_id, Tag.user_id == user.id)
        )
        stmt = stmt.where(Document.id.in_(tagged))
    if collection_id is not None:
        in_collection = (
            select(CollectionItem.document_id)
            .join(Collection, Collection.id == CollectionItem.collection_id)
            .where(CollectionItem.collection_id == collection_id, Collection.user_id == user.id)
        )
        stmt = stmt.where(Document.id.in_(in_collection))
    if starred is not None:
        stmt = stmt.where(_flag_filter(user.id, DocumentFlag.starred, starred))
    if archived is not None:
        stmt = stmt.where(_flag_filter(user.id, DocumentFlag.archived, archived))
    if search:
        escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        stmt = stmt.where(
            or_(Document.title.ilike(pattern, escape="\\"), Document.url.ilike(pattern, escape="\\"))
        )
    rows = db.execute(
        stmt.order_by(Document.last_seen_at.desc().nullslast(), Document.id)
        .limit(page.limit)
        .offset(page.offset)
    ).all()
    tags, collections = document_refs(db, user.id, [doc.id for doc, _ in rows])
    out: list[DocumentOut] = []
    for doc, doc_source_type in rows:
        item = DocumentOut.model_validate(doc)
        item.source_type = doc_source_type
        item.tags = tags.get(doc.id, [])
        item.collections = collections.get(doc.id, [])
        out.append(item)
    return out


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
    if (
        before is None
        or after is None
        or before.document_id != document_id
        or after.document_id != document_id
        or before.user_id != user.id
        or after.user_id != user.id
    ):
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

# --- captures ------------------------------------------------------------


class CaptureIn(BaseModel):
    """A quick capture: a URL to fetch, or text to keep as a note."""

    url: str | None = None
    text: str | None = None
    title: str | None = None


class CaptureOut(BaseModel):
    source_id: uuid.UUID
    source_type: str
    name: str
    url: str | None = None
    job_id: int | None = None


@router.post(
    "/captures",
    response_model=CaptureOut,
    status_code=status.HTTP_201_CREATED,
    summary="Capture a URL or a note",
)
def post_capture(
    payload: CaptureIn,
    user: CurrentUser = Depends(require_scope("write")),
    db: Session = Depends(get_db),
) -> CaptureOut:
    """Store a URL or a note and queue whatever work it implies.

    A URL becomes a webpage source and is fetched; text becomes a note and is indexed
    immediately. An excerpt alongside a URL is stored as its own clip, so the user's
    own words are never lost to a fetch that fails.
    """
    from app.ingestion.sources import create_source
    from app.ingestion.types import ExtractedDocument
    from app.ingestion.versions import upsert_document_version

    raw_url = (payload.url or "").strip()
    raw_text = (payload.text or "").strip()
    if not raw_url and not raw_text:
        raise DomainValidationError("provide a url or some text", code="empty_capture")

    job_id: int | None = None
    source = None

    if raw_url:
        from app.ingestion.netguard import SSRFError, validate_url

        try:
            validate_url(raw_url)
        except SSRFError as exc:
            raise DomainValidationError(exc.message, code=exc.code) from exc
        source = create_source(db, user.id, source_type="webpage", name=payload.title or raw_url, url=raw_url)
        job = q.enqueue(
            db,
            "ingest_source",
            {"source_id": str(source.id)},
            user_id=user.id,
            dedupe_key=f"ingest:{source.id}",
        )
        db.commit()
        job_id = job.id
    else:
        source = create_source(db, user.id, source_type="note", name=payload.title or raw_text[:80])
        db.refresh(source)

    if raw_text:
        note_source = (
            source
            if not raw_url
            else create_source(db, user.id, source_type="note", name=payload.title or f"Excerpt: {raw_url}")
        )
        excerpt = ExtractedDocument(title=payload.title or note_source.name, text=raw_text, url=None)
        res = upsert_document_version(db, source=note_source, extracted=excerpt, origin="upload")
        db.commit()
        if res.version:
            note_job = q.enqueue(
                db,
                "index_version",
                {"version_id": res.version.id},
                user_id=user.id,
                dedupe_key=f"index-note:{note_source.id}",
            )
            db.commit()
            if not job_id:
                job_id = note_job.id

    return CaptureOut(
        source_id=source.id,
        source_type=source.source_type,
        name=source.name,
        url=source.url,
        job_id=job_id,
    )


@router.post(
    "/captures/parse",
    summary="Preview a capture without saving it",
)
async def parse_capture(
    request: Request,
    user: CurrentUser = Depends(require_scope("read")),
) -> dict:
    """Preview a URL, some text, or an uploaded file. Nothing is stored or queued.

    JSON `{url}` fetches the page through netguard. JSON `{text}` reports sentence and
    candidate-claim counts. A multipart `file` runs the same extraction an upload would,
    under the same type and size limits, and is then discarded.
    """
    if request.headers.get("content-type", "").startswith("multipart/form-data"):
        form = await request.form()
        upload = form.get("file")
        if not isinstance(upload, FormUploadFile):
            raise DomainValidationError("provide a file", code="empty_capture")
        data = await _read_upload_limited(upload)
        return await run_in_threadpool(_preview_file, upload.filename or "", data)
    try:
        payload = CaptureIn.model_validate(await request.json())
    except ValueError as exc:
        raise DomainValidationError("send a JSON capture or a multipart file", code="bad_body") from exc
    if (payload.url or "").strip():
        return await run_in_threadpool(_preview_url, payload)
    return await run_in_threadpool(_preview_text, payload)


async def _read_upload_limited(upload: FormUploadFile) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while chunk := await upload.read(1024 * 1024):
        total += len(chunk)
        if total > MAX_UPLOAD_BYTES:
            raise PayloadTooLargeError(
                f"file is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)}MB", code="file_too_large"
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _preview_text(payload: CaptureIn) -> dict:
    from app.claims.heuristic_extract import is_claim_sentence
    from app.ingestion.segment import split_sentences

    text = (payload.text or "").strip()
    sentences = [span.text_of(text) for span in split_sentences(text)]
    first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
    return {
        "kind": "text",
        "title_guess": (payload.title or first_line.lstrip("# ").strip())[:120],
        "sentences": len(sentences),
        "candidate_claims": sum(1 for sentence in sentences if is_claim_sentence(sentence)),
        "preview": text[:2000],
    }


def _preview_file(filename: str, data: bytes) -> dict:
    from app.ingestion.fetch import FetchError
    from app.ingestion.pdf import extract_pdf, extract_text_file

    extension = Path(filename).suffix.lower()
    if extension not in UPLOAD_EXTENSIONS:
        raise DomainValidationError(
            f"unsupported file type {extension or '(none)'}; allowed: {', '.join(sorted(UPLOAD_EXTENSIONS))}",
            code="bad_file_type",
        )
    try:
        doc = extract_pdf(data) if extension == ".pdf" else extract_text_file(data, filename=filename)
    except FetchError as exc:
        raise DomainValidationError(str(exc), code=exc.code) from exc
    return {
        "kind": "file",
        "filename": filename,
        "title": doc.title or filename,
        # Pages that yielded text; a scanned PDF with no text layer reports 0 here.
        "pages": len(doc.page_map or []) if extension == ".pdf" else None,
        "chars": len(doc.text),
        "preview": doc.text[:2000],
        "warnings": doc.warnings,
    }


def _preview_url(payload: CaptureIn) -> dict:
    """Extract a readable preview from a URL so the user can confirm before saving."""
    from app.ingestion.fetch import FetchError, fetch
    from app.ingestion.netguard import SSRFError, validate_url
    from app.ingestion.webpage import extract_webpage

    raw = (payload.url or "").strip()
    try:
        validate_url(raw)
    except SSRFError as exc:
        raise DomainValidationError(exc.message, code=exc.code) from exc

    try:
        page = fetch(raw)
    except FetchError as exc:
        if exc.code == "ssrf_blocked":
            raise DomainValidationError(str(exc), code="ssrf_blocked") from exc
        # A preview that cannot be fetched is still worth saving, so say what went
        # wrong rather than refusing.
        return {"preview": "", "title": payload.title or raw, "url": raw, "error": exc.code}
    doc = extract_webpage(page.content.decode("utf-8", errors="replace"), page.final_url)
    return {
        "preview": doc.text[:2000],
        "title": doc.title or payload.title or raw,
        "url": page.final_url,
    }


__all__ = ["DomainValidationError", "uuid", "router"]
