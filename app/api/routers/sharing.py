"""Read-only share links: private management and one public, token-holding read.

Every route, public one included, answers 404 while `sharing_enabled` is off.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, get_db, require_scope
from app.core.config import get_settings
from app.core.errors import NotFoundError
from app.core.ratelimit import enforce
from app.schemas.sharing import PublicShare, ShareCreate, ShareCreated, ShareOut
from app.sharing import service


def _require_sharing() -> None:
    if not get_settings().sharing_enabled:
        raise NotFoundError("not found")


router = APIRouter(tags=["sharing"], dependencies=[Depends(_require_sharing)])


@router.post("/shares", response_model=ShareCreated, status_code=201, summary="Create a read-only share link")
def post_share(
    payload: ShareCreate,
    user: CurrentUser = Depends(require_scope("write")),
    db: Session = Depends(get_db),
) -> ShareCreated:
    row, token = service.create_share(db, user.id, payload.kind, payload.target_id, payload.expires_in_days)
    return ShareCreated(**ShareOut.model_validate(row).model_dump(), token=token)


@router.get("/shares", response_model=list[ShareOut], summary="List your share links (no tokens)")
def get_shares(
    user: CurrentUser = Depends(require_scope("read")),
    db: Session = Depends(get_db),
) -> list[ShareOut]:
    return [ShareOut.model_validate(r) for r in service.list_shares(db, user.id)]


@router.delete("/shares/{share_id}", status_code=204, summary="Revoke a share link")
def delete_share(
    share_id: int,
    user: CurrentUser = Depends(require_scope("write")),
    db: Session = Depends(get_db),
) -> Response:
    service.revoke_share(db, user.id, share_id)
    return Response(status_code=204)


@router.get("/public/shares/{token}", response_model=PublicShare, summary="Read a shared page or answer")
def get_public_share(
    token: str,
    request: Request,
    db: Session = Depends(get_db),
) -> PublicShare:
    # Unauthenticated on purpose: the token is the credential. Rate limited per client host.
    client_host = request.client.host if request.client else "unknown"
    enforce(db, f"/v1/public/shares:{client_host}", get_settings().rate_limit_verify)
    share = service.find_live_share(db, token)
    if share is None:
        raise NotFoundError("not found")
    snapshot = service.public_snapshot(db, share)
    if snapshot is None:
        raise NotFoundError("not found")
    return snapshot


__all__ = ["router"]
