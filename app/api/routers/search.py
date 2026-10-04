"""Search endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, get_db, require_scope
from app.retrieval.hybrid import hybrid_search
from app.retrieval.types import HitOut, SearchRequest, SearchResponse

router = APIRouter(tags=["search"])


@router.post("/search", response_model=SearchResponse, summary="Hybrid search")
def search(
    payload: SearchRequest,
    user: CurrentUser = Depends(require_scope("read")),
    db: Session = Depends(get_db),
) -> SearchResponse:
    result = hybrid_search(
        db,
        user.id,
        payload.query,
        k=payload.k,
        mode=payload.mode,
        rerank=payload.rerank,
        filters=payload.filters,
    )
    return SearchResponse(
        hits=[HitOut.model_validate(hit) for hit in result.hits],
        mode_effective=result.mode_effective,
        timings_ms={k: round(v, 2) for k, v in result.timings_ms.items()},
        total_candidates=result.total_candidates,
    )


__all__ = ["router"]
