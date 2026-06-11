from __future__ import annotations

import json
import shutil
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import Depends, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.auth.dependencies import User, get_current_user, require_auth
from app.config import settings
from app.database import get_db, init_db
from app.models import Briefing, Collection, Document, DocumentChunk, IngestionRun, ScheduledJob, Source, Tag
from app.retrieval.search import retrieve_documents
from app.schemas import (
    BriefingRead,
    BriefingRequest,
    CaptureCreate,
    CaptureCreateResult,
    CaptureParseRead,
    CaptureParseRequest,
    ChunkRead,
    CollectionCreate,
    CollectionRead,
    CollectionUpdate,
    ConversationSaveRequest,
    ConversationSaveResponse,
    DocumentRead,
    IngestionRunRead,
    IndexStatusRead,
    ItemLink,
    ScheduleCreate,
    ScheduleRead,
    ScheduleRunRead,
    ScheduleUpdate,
    SearchHit,
    SearchRequest,
    SearchResponse,
    SourceCreate,
    SourceRead,
    SourceUpdate,
    TagCreate,
    TagRead,
    TagUpdate,
)
from app.services.briefing import generate_briefing
from app.services.captures import create_capture, parse_capture_text
from app.services.citations import answer_with_citations, citation_label
from app.services.conversations import save_conversation_markdown
from app.services.schedules import (
    create_schedule,
    delete_schedule,
    run_schedule_now,
    schedule_payload,
    scheduler_poller,
    update_schedule as update_schedule_job,
)
from app.services.semantic_index import rebuild_semantic_index, semantic_index_status
from app.services.library import (
    add_collection_item,
    add_item_tag,
    create_collection,
    create_tag,
    delete_collection,
    delete_tag,
    list_documents as list_library_documents,
    remove_collection_item,
    remove_item_tag,
    update_collection,
    update_tag,
)
from app.services.pipeline import (
    SourcePausedError,
    create_source,
    delete_source,
    ingest_source,
    platform_stats,
    update_source,
)
from app.storage import get_storage


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    poller_task = asyncio.create_task(scheduler_poller())
    try:
        yield
    finally:
        poller_task.cancel()
        with suppress(asyncio.CancelledError):
            await poller_task


import asyncio

app = FastAPI(title="SourceHero AI", version="0.7.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def root() -> dict:
    return {"name": "SourceHero AI", "status": "ready"}


@app.get("/health")
def health(db: Session = Depends(get_db), user: User | None = Depends(get_current_user)) -> dict:
    if not user:
        return {"name": "SourceHero AI", "status": "ready", "version": app.version}
    index_status = semantic_index_status(db)
    return {
        "name": "SourceHero AI",
        "status": "ready",
        "version": app.version,
        "stats": platform_stats(db, user.id),
        "semantic_index_enabled": index_status.enabled,
        "embedding_backend": index_status.backend,
        "indexed_chunks": index_status.indexed_chunks,
        "pending_chunks": index_status.pending_chunks,
    }


@app.get("/stats")
def stats(db: Session = Depends(get_db), user: User = Depends(require_auth)) -> dict:
    return platform_stats(db, user.id)


@app.get("/index/status", response_model=IndexStatusRead)
def index_status(db: Session = Depends(get_db), user: User = Depends(require_auth)):
    return semantic_index_status(db).__dict__


@app.post("/index/rebuild")
def rebuild_index(db: Session = Depends(get_db), user: User = Depends(require_auth)) -> dict:
    try:
        return rebuild_semantic_index(db, user.id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/settings")
def get_settings(user: User = Depends(require_auth)) -> dict:
    from app.services.user_settings import public_settings

    return public_settings()


@app.post("/settings")
def update_settings(payload: dict, user: User = Depends(require_auth)) -> dict:
    from app.services.user_settings import clear_openai_key, save_user_config

    incoming: dict = {}
    if "openai_api_key" in payload:
        key = (payload.get("openai_api_key") or "").strip()
        if key:
            incoming["openai_api_key"] = key
        else:
            clear_openai_key()
    if "openai_model" in payload:
        model = (payload.get("openai_model") or "").strip()
        if model:
            incoming["openai_model"] = model
    if incoming:
        save_user_config(incoming)
    from app.services.user_settings import public_settings

    return public_settings()


@app.post("/settings/test-openai")
def test_openai_settings(user: User = Depends(require_auth)) -> dict:
    from app.services.user_settings import effective_openai_key, effective_openai_model

    key = effective_openai_key()
    model = effective_openai_model()
    if not key:
        raise HTTPException(status_code=400, detail="No OpenAI API key configured.")
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise HTTPException(
            status_code=500,
            detail="The `openai` Python package is not installed. Reinstall SourceHero dependencies.",
        ) from exc

    try:
        client = OpenAI(api_key=key)
        response = client.responses.create(model=model, input="Reply with the single word: ok")
        text = getattr(response, "output_text", "") or ""
        return {"ok": True, "model": model, "sample": text.strip()[:120]}
    except Exception as exc:
        cls = exc.__class__.__name__
        msg = str(exc)[:200]
        if "AuthenticationError" in cls or "401" in msg:
            detail = "Invalid API key. Double-check that you pasted it correctly and that it is active."
        elif "RateLimitError" in cls or "429" in msg:
            detail = "Rate limited by OpenAI. Wait a moment and try again."
        elif "APIConnectionError" in cls or "Connection" in msg:
            detail = "Could not reach OpenAI. Check your network connection."
        elif "NotFoundError" in cls or "model" in msg.lower():
            detail = f"Model `{model}` is not available on this key. Pick another model and save again."
        elif "PermissionDeniedError" in cls or "403" in msg:
            detail = "This key does not have permission to use that model."
        else:
            detail = f"OpenAI call failed ({cls}): {msg}"
        raise HTTPException(status_code=400, detail=detail) from exc


@app.post("/sources", response_model=SourceRead)
def add_source(payload: SourceCreate, db: Session = Depends(get_db), user: User = Depends(require_auth)):
    try:
        return create_source(db, user.id, payload.source_type, payload.name, payload.url, payload.local_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/sources", response_model=list[SourceRead])
def list_sources(db: Session = Depends(get_db), user: User = Depends(require_auth)):
    return db.scalars(
        select(Source).where(Source.user_id == user.id).order_by(desc(Source.created_at))
    ).all()


@app.patch("/sources/{source_id}", response_model=SourceRead)
def patch_source(source_id: int, payload: SourceUpdate, db: Session = Depends(get_db), user: User = Depends(require_auth)):
    try:
        return update_source(db, user.id, source_id, payload.name, payload.url, payload.local_path, payload.status)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/sources/{source_id}")
def remove_source(source_id: int, db: Session = Depends(get_db), user: User = Depends(require_auth)) -> dict:
    try:
        delete_source(db, user.id, source_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "deleted", "source_id": source_id}


@app.post("/sources/{source_id}/ingest", response_model=IngestionRunRead)
def run_ingestion(source_id: int, db: Session = Depends(get_db), user: User = Depends(require_auth)):
    try:
        return ingest_source(db, user.id, source_id)
    except SourcePausedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        status_code = 404 if "not found" in str(exc).lower() else 400
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc


@app.get("/ingestion-runs", response_model=list[IngestionRunRead])
def list_ingestion_runs(db: Session = Depends(get_db), user: User = Depends(require_auth)):
    return db.scalars(
        select(IngestionRun).where(IngestionRun.user_id == user.id).order_by(desc(IngestionRun.started_at))
    ).all()


@app.get("/documents", response_model=list[DocumentRead])
def list_documents(
    source_ids: list[int] | None = Query(default=None),
    source_type: str | None = None,
    collection_id: int | None = None,
    tags: list[str] | None = Query(default=None),
    db: Session = Depends(get_db),
    user: User = Depends(require_auth),
):
    return [
        _document_read(document, source, include_text=False)
        for document, source in list_library_documents(db, user.id, source_ids, source_type, collection_id, tags)
    ]


@app.get("/documents/{document_id}", response_model=DocumentRead)
def get_document(document_id: int, db: Session = Depends(get_db), user: User = Depends(require_auth)):
    row = db.execute(
        select(Document, Source)
        .join(Source, Document.source_id == Source.id)
        .where(Document.id == document_id, Document.user_id == user.id)
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail=f"Document not found: {document_id}")
    document, source = row
    return _document_read(document, source, include_text=True)


@app.get("/documents/{document_id}/chunks", response_model=list[ChunkRead])
def get_document_chunks(document_id: int, db: Session = Depends(get_db), user: User = Depends(require_auth)):
    doc = db.get(Document, document_id)
    if not doc or doc.user_id != user.id:
        raise HTTPException(status_code=404, detail=f"Document not found: {document_id}")
    return db.scalars(
        select(DocumentChunk).where(DocumentChunk.document_id == document_id).order_by(DocumentChunk.chunk_index)
    ).all()


@app.post("/search", response_model=SearchResponse)
def search(payload: SearchRequest, db: Session = Depends(get_db), user: User = Depends(require_auth)):
    bundle = retrieve_documents(
        db,
        user_id=user.id,
        query=payload.query,
        top_k=payload.top_k,
        source_ids=payload.source_ids,
        source_type=payload.source_type,
        collection_id=payload.collection_id,
        tags=payload.tags,
        retrieval_mode=payload.retrieval_mode,
    )
    return SearchResponse(
        query=payload.query,
        answer_markdown=answer_with_citations(payload.query, bundle.hits),
        effective_retrieval_mode=bundle.effective_retrieval_mode,
        hits=[
            SearchHit(
                chunk_id=hit.chunk_id,
                document_id=hit.document_id,
                source_id=hit.source_id,
                title=hit.title,
                source_name=hit.source_name,
                source_type=hit.source_type,
                url=hit.url,
                local_path=hit.local_path,
                score=hit.score,
                snippet=hit.snippet,
                citation=citation_label(hit, index),
                metadata=hit.metadata,
            )
            for index, hit in enumerate(bundle.hits, start=1)
        ],
    )


@app.post("/conversations/save", response_model=ConversationSaveResponse)
def save_conversation(payload: ConversationSaveRequest, db: Session = Depends(get_db), user: User = Depends(require_auth)):
    try:
        return save_conversation_markdown(db, user.id, payload.title, payload.markdown)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/captures/parse", response_model=CaptureParseRead)
def parse_capture(payload: CaptureParseRequest):
    try:
        preview = parse_capture_text(payload.raw_text)
        return preview.__dict__
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/captures", response_model=CaptureCreateResult)
def save_capture(payload: CaptureCreate, db: Session = Depends(get_db), user: User = Depends(require_auth)):
    try:
        return create_capture(
            db,
            user.id,
            title=payload.title,
            source_url=payload.source_url,
            excerpt_text=payload.excerpt_text,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/collections", response_model=list[CollectionRead])
def list_collections(db: Session = Depends(get_db), user: User = Depends(require_auth)):
    return db.scalars(
        select(Collection).where(Collection.user_id == user.id).order_by(Collection.name)
    ).all()


@app.post("/collections", response_model=CollectionRead)
def add_collection(payload: CollectionCreate, db: Session = Depends(get_db), user: User = Depends(require_auth)):
    try:
        return create_collection(db, user.id, payload.name, payload.description)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.patch("/collections/{collection_id}", response_model=CollectionRead)
def patch_collection(collection_id: int, payload: CollectionUpdate, db: Session = Depends(get_db), user: User = Depends(require_auth)):
    try:
        return update_collection(db, user.id, collection_id, payload.name, payload.description)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/collections/{collection_id}")
def remove_collection(collection_id: int, db: Session = Depends(get_db), user: User = Depends(require_auth)) -> dict:
    try:
        delete_collection(db, user.id, collection_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "deleted", "collection_id": collection_id}


@app.post("/collections/{collection_id}/items")
def link_collection_item(collection_id: int, payload: ItemLink, db: Session = Depends(get_db), user: User = Depends(require_auth)) -> dict:
    try:
        link = add_collection_item(db, user.id, collection_id, payload.item_type, payload.item_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"id": link.id, "collection_id": collection_id, "item_type": payload.item_type, "item_id": payload.item_id}


@app.delete("/collections/{collection_id}/items")
def unlink_collection_item(collection_id: int, item_type: str, item_id: int, db: Session = Depends(get_db), user: User = Depends(require_auth)) -> dict:
    try:
        remove_collection_item(db, user.id, collection_id, item_type, item_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "deleted", "collection_id": collection_id, "item_type": item_type, "item_id": item_id}


@app.get("/tags", response_model=list[TagRead])
def list_tags(db: Session = Depends(get_db), user: User = Depends(require_auth)):
    return db.scalars(
        select(Tag).where(Tag.user_id == user.id).order_by(Tag.name)
    ).all()


@app.post("/tags", response_model=TagRead)
def add_tag(payload: TagCreate, db: Session = Depends(get_db), user: User = Depends(require_auth)):
    try:
        return create_tag(db, user.id, payload.name, payload.color)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.patch("/tags/{tag_id}", response_model=TagRead)
def patch_tag(tag_id: int, payload: TagUpdate, db: Session = Depends(get_db), user: User = Depends(require_auth)):
    try:
        return update_tag(db, user.id, tag_id, payload.name, payload.color)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/tags/{tag_id}")
def remove_tag(tag_id: int, db: Session = Depends(get_db), user: User = Depends(require_auth)) -> dict:
    try:
        delete_tag(db, user.id, tag_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "deleted", "tag_id": tag_id}


@app.post("/tags/{tag_id}/items")
def link_tag_item(tag_id: int, payload: ItemLink, db: Session = Depends(get_db), user: User = Depends(require_auth)) -> dict:
    try:
        link = add_item_tag(db, user.id, tag_id, payload.item_type, payload.item_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"id": link.id, "tag_id": tag_id, "item_type": payload.item_type, "item_id": payload.item_id}


@app.delete("/tags/{tag_id}/items")
def unlink_tag_item(tag_id: int, item_type: str, item_id: int, db: Session = Depends(get_db), user: User = Depends(require_auth)) -> dict:
    try:
        remove_item_tag(db, user.id, tag_id, item_type, item_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "deleted", "tag_id": tag_id}


@app.post("/briefings", response_model=BriefingRead)
def create_briefing(payload: BriefingRequest, db: Session = Depends(get_db), user: User = Depends(require_auth)):
    return generate_briefing(
        db,
        user.id,
        payload.topic,
        top_k=payload.top_k,
        source_ids=payload.source_ids,
        source_type=payload.source_type,
        collection_id=payload.collection_id,
        tags=payload.tags,
    )


@app.get("/briefings", response_model=list[BriefingRead])
def list_briefings(db: Session = Depends(get_db), user: User = Depends(require_auth)):
    return db.scalars(
        select(Briefing).where(Briefing.user_id == user.id).order_by(desc(Briefing.created_at))
    ).all()


@app.get("/schedules", response_model=list[ScheduleRead])
def list_schedules(db: Session = Depends(get_db), user: User = Depends(require_auth)):
    jobs = db.scalars(
        select(ScheduledJob)
        .where(ScheduledJob.user_id == user.id)
        .order_by(ScheduledJob.next_run_at, ScheduledJob.id)
    ).all()
    return [_schedule_read(job) for job in jobs]


@app.post("/schedules", response_model=ScheduleRead)
def add_schedule(payload: ScheduleCreate, db: Session = Depends(get_db), user: User = Depends(require_auth)):
    try:
        job = create_schedule(
            db,
            user.id,
            job_type=payload.job_type,
            name=payload.name or "",
            schedule_kind=payload.schedule_kind,
            time_local=payload.time_local,
            day_of_week=payload.day_of_week,
            payload=payload.payload,
        )
        return _schedule_read(job)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.patch("/schedules/{schedule_id}", response_model=ScheduleRead)
def patch_schedule(schedule_id: int, payload: ScheduleUpdate, db: Session = Depends(get_db), user: User = Depends(require_auth)):
    try:
        job = update_schedule_job(
            db,
            user.id,
            schedule_id,
            name=payload.name,
            status=payload.status,
            schedule_kind=payload.schedule_kind,
            time_local=payload.time_local,
            day_of_week=payload.day_of_week,
            payload=payload.payload,
        )
        return _schedule_read(job)
    except ValueError as exc:
        status_code = 404 if "not found" in str(exc).lower() else 400
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc


@app.delete("/schedules/{schedule_id}")
def remove_schedule(schedule_id: int, db: Session = Depends(get_db), user: User = Depends(require_auth)) -> dict:
    try:
        delete_schedule(db, user.id, schedule_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "deleted", "schedule_id": schedule_id}


@app.post("/schedules/{schedule_id}/run-now", response_model=ScheduleRunRead)
def run_schedule(schedule_id: int, db: Session = Depends(get_db), user: User = Depends(require_auth)):
    try:
        run = run_schedule_now(db, user.id, schedule_id)
        return _schedule_run_read(run)
    except ValueError as exc:
        status_code = 404 if "not found" in str(exc).lower() else 400
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc


@app.post("/upload-pdf", response_model=SourceRead)
def upload_pdf(name: str, file: UploadFile = File(...), db: Session = Depends(get_db), user: User = Depends(require_auth)):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF uploads are supported")

    storage = get_storage()
    safe_name = Path(file.filename).name
    r2_key = f"uploads/{user.id}/{safe_name}"
    storage.upload(r2_key, file.file, content_type="application/pdf")

    return create_source(db, user.id, "pdf", name=name or safe_name, r2_key=r2_key)


def _document_read(document: Document, source: Source, include_text: bool) -> dict:
    return {
        "id": document.id,
        "source_id": document.source_id,
        "title": document.title,
        "url": document.url,
        "author": document.author,
        "published_at": document.published_at,
        "fetched_at": document.fetched_at,
        "content_hash": document.content_hash,
        "clean_text": document.clean_text if include_text else None,
        "source_name": source.name,
        "source_type": source.source_type,
    }


def _schedule_read(job: ScheduledJob) -> dict:
    return {
        "id": job.id,
        "job_type": job.job_type,
        "name": job.name,
        "status": job.status,
        "schedule_kind": job.schedule_kind,
        "time_local": job.time_local,
        "day_of_week": job.day_of_week,
        "payload": schedule_payload(job),
        "last_run_at": job.last_run_at,
        "next_run_at": job.next_run_at,
        "last_error": job.last_error,
        "created_at": job.created_at,
    }


def _schedule_run_read(run) -> dict:
    return {
        "id": run.id,
        "job_id": run.job_id,
        "started_at": run.started_at,
        "ended_at": run.ended_at,
        "status": run.status,
        "summary": run.summary,
        "error_message": run.error_message,
    }
