"""`POST /v1/ask` (streaming or JSON) and `POST /v1/verify`."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, get_db, require_scope
from app.core.config import get_settings
from app.core.ratelimit import enforce
from app.schemas.verify import AskRequest, VerifyRequest, VerifyResponse
from app.verify.verifier import verify_text

router = APIRouter(tags=["verify"])


@router.post("/verify", response_model=VerifyResponse, summary="Check sentences against evidence")
def post_verify(
    payload: VerifyRequest,
    user: CurrentUser = Depends(require_scope("read")),
    db: Session = Depends(get_db),
) -> VerifyResponse:
    enforce(db, f"/v1/verify:{user.id}", get_settings().rate_limit_verify)
    result = verify_text(
        db,
        user.id,
        payload.text,
        mode=payload.mode,
        citations=payload.citations,
        k=payload.k,
    )
    _record(db, user.id, payload.text, result)
    return VerifyResponse(**result.as_dict())


def _record(db: Session, user_id: uuid.UUID, text: str, result) -> None:
    """Persist the run so a verdict can be audited after the fact.

    Written on its own session commit: the request session may still be rolled back
    by a test, but the record is the durable artefact.
    """
    from app.models.answer import Verification

    db.add(Verification(user_id=user_id, input_text=text, result=result.as_dict(), method=result.method))
    db.commit()


@router.post("/ask", summary="Ask a grounded question", response_model=None)
def post_ask(
    payload: AskRequest,
    stream: bool = Query(True, description="SSE stream, or one JSON body"),
    user: CurrentUser = Depends(require_scope("read")),
    db: Session = Depends(get_db),
):
    from app.verify.answer import answer_json, answer_stream

    enforce(db, f"/v1/ask:{user.id}", get_settings().rate_limit_ask)
    if stream:
        return answer_stream(payload.question, user.id, k=payload.k)
    return answer_json(payload.question, user.id, db, k=payload.k)


__all__ = ["router"]
