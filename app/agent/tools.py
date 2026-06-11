from __future__ import annotations

from sqlalchemy.orm import Session

from app.retrieval.search import search_documents as run_search
from app.services.briefing import generate_briefing as run_briefing
from app.services.citations import answer_with_citations
from app.services.pipeline import create_source, ingest_source as run_ingest


def ingest_source(db: Session, user_id: str, source_type: str, source_value: str, name: str | None = None) -> dict:
    source = create_source(
        db,
        user_id=user_id,
        source_type=source_type,
        name=name or source_value,
        url=source_value if source_type in {"rss", "webpage"} else None,
        local_path=source_value if source_type == "pdf" else None,
    )
    run = run_ingest(db, user_id, source.id)
    return {"source_id": source.id, "run_id": run.id, "status": run.status}


def search_documents(
    db: Session,
    user_id: str,
    query: str,
    top_k: int = 5,
    source_ids: list[int] | None = None,
    source_type: str | None = None,
    collection_id: int | None = None,
    tags: list[str] | None = None,
) -> list[dict]:
    return [
        hit.__dict__
        for hit in run_search(
            db,
            user_id=user_id,
            query=query,
            top_k=top_k,
            source_ids=source_ids,
            source_type=source_type,
            collection_id=collection_id,
            tags=tags,
        )
    ]


def summarize_with_citations(db: Session, user_id: str, query: str, top_k: int = 5) -> str:
    return answer_with_citations(query, run_search(db, user_id=user_id, query=query, top_k=top_k))


def generate_briefing(db: Session, user_id: str, topic: str, top_k: int = 8) -> str:
    return run_briefing(db, user_id=user_id, topic=topic, top_k=top_k).answer_markdown
