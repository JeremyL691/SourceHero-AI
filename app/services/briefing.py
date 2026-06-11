from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.models import Briefing
from app.retrieval.search import retrieve_documents
from app.services.citations import format_sources, serialize_citations


def generate_briefing(
    db: Session,
    user_id: str,
    topic: str,
    top_k: int = 8,
    source_ids: list[int] | None = None,
    source_type: str | None = None,
    collection_id: int | None = None,
    tags: list[str] | None = None,
) -> Briefing:
    bundle = retrieve_documents(
        db,
        user_id=user_id,
        query=topic,
        top_k=top_k,
        source_ids=source_ids,
        source_type=source_type,
        collection_id=collection_id,
        tags=tags,
        retrieval_mode="hybrid",
    )
    hits = bundle.hits
    if not hits:
        answer = (
            f"# Briefing: {topic}\n\n"
            "No relevant indexed evidence was found. Ingest sources related to this topic before generating a briefing."
        )
        citations: list[dict] = []
    else:
        bullets = [f"- {hit.snippet} [{index}]" for index, hit in enumerate(hits, start=1)]
        answer = "\n".join(
            [
                f"# Briefing: {topic}",
                "",
                f"_Retrieval mode: `{bundle.effective_retrieval_mode}`_",
                "",
                "## Evidence Summary",
                *bullets,
                "",
                format_sources(hits),
            ]
        )
        citations = serialize_citations(hits)

    briefing = Briefing(
        user_id=user_id,
        query=topic,
        answer_markdown=answer,
        citation_json=json.dumps(citations, ensure_ascii=False),
    )
    db.add(briefing)
    db.commit()
    db.refresh(briefing)
    return briefing
