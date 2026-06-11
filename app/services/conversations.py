from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ingestion.base import ExtractedDocument
from app.ingestion.chunking import clean_text
from app.ingestion.quality import store_extracted_documents
from app.models import Source
from app.services.pipeline import create_source
from app.services.semantic_index import index_new_chunks


CONVERSATION_SOURCE_NAME = "Saved Conversations"


def save_conversation_markdown(db: Session, user_id: str, title: str, markdown: str) -> dict:
    normalized_title = clean_text(title)[:160] or "Saved Conversation"
    normalized_markdown = markdown.strip()
    if not normalized_markdown:
        raise ValueError("Conversation markdown cannot be empty")

    source = _get_or_create_conversation_source(db, user_id)
    stats = store_extracted_documents(
        db,
        source,
        [
            ExtractedDocument(
                title=normalized_title,
                raw_text=normalized_markdown,
                clean_text=normalized_markdown,
                metadata={"source_kind": "conversation"},
            )
        ],
        user_id=user_id,
    )
    db.commit()
    try:
        index_new_chunks(db, [chunk_id for chunk_id in stats.get("chunk_ids_inserted", []) if chunk_id is not None])
    except Exception:
        pass
    return {"source_id": source.id, "status": "saved", **stats}


def _get_or_create_conversation_source(db: Session, user_id: str) -> Source:
    source = db.scalar(
        select(Source).where(
            Source.source_type == "conversation",
            Source.name == CONVERSATION_SOURCE_NAME,
            Source.user_id == user_id,
        )
    )
    if source:
        return source
    return create_source(db, user_id, "conversation", CONVERSATION_SOURCE_NAME)
