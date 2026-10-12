"""Request-scoped dependencies: the database session, the current user, scopes, pagination.

One dependency (`current_user`) understands both credentials we issue -- a JWT and a
`sh_pat_...` personal access token -- so no router needs to know which was used.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass

import jwt
from fastapi import Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import SessionLocal, get_db
from app.core.errors import PermissionDeniedError, UnauthenticatedError
from app.core.security import decode_access_token
from app.core.time import utcnow
from app.models.user import ApiToken, User

PAT_PREFIX = "sh_pat_"


@dataclass(frozen=True)
class CurrentUser:
    id: uuid.UUID
    email: str
    is_admin: bool
    scopes: frozenset[str]
    via: str  # "jwt" | "pat" | "supabase"

    def has(self, scope: str) -> bool:
        return scope in self.scopes


def get_session_factory() -> Callable[[], Session]:
    """Overridden by SSE generators and tests, which need a differently-scoped session."""
    return SessionLocal


def _bearer(request: Request) -> str | None:
    header = request.headers.get("authorization") or ""
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return None


def _resolve_pat(db: Session, token: str) -> tuple[User, frozenset[str]] | None:
    import hashlib

    token_hash = hashlib.sha256(token.encode()).hexdigest()
    row = db.execute(select(ApiToken).where(ApiToken.token_hash == token_hash)).scalar_one_or_none()
    if row is None or row.revoked_at is not None:
        return None
    # Writing on every request would be a hot UPDATE; once a minute is plenty.
    now = utcnow()
    if row.last_used_at is None or (now - row.last_used_at).total_seconds() > 60:
        row.last_used_at = now
        db.commit()
    user = db.get(User, row.user_id)
    if user is None:
        return None
    return user, frozenset(row.scopes or ())


def _user_from_supabase(db: Session, token: str) -> User | None:
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from app.core.supabase import verify_supabase_jwt

    external_id = verify_supabase_jwt(token)
    if external_id is None:
        return None
    user = db.execute(select(User).where(User.external_id == external_id)).scalar_one_or_none()
    if user is None:
        # First time we see this subject: create the local row using upsert
        # so concurrent first logins do not raise IntegrityError.
        settings = get_settings()
        email = f"{external_id}@{settings.supabase_url.split('//')[-1].split('/')[0]}"
        stmt = (
            pg_insert(User)
            .values(
                id=uuid.uuid4(),
                email=email,
                display_name=None,
                auth_provider="supabase",
                external_id=external_id,
                is_admin=False,
            )
            .on_conflict_do_nothing(index_elements=["external_id"])
        )
        db.execute(stmt)
        db.commit()
        user = db.execute(select(User).where(User.external_id == external_id)).scalar_one_or_none()
    return user


def get_current_user(request: Request, db: Session = Depends(get_db)) -> CurrentUser:
    token = _bearer(request)
    if not token:
        raise UnauthenticatedError("authentication required", code="unauthenticated")

    settings = get_settings()
    if token.startswith(PAT_PREFIX):
        resolved = _resolve_pat(db, token)
        if resolved is None:
            raise UnauthenticatedError("invalid or revoked token", code="invalid_token")
        user, scopes = resolved
        request.state.user_id = str(user.id)  # for access logs; never the raw id in logs
        return CurrentUser(user.id, user.email, user.is_admin, scopes, "pat")

    if settings.auth_mode == "supabase":
        user = _user_from_supabase(db, token)
        if user is None:
            raise UnauthenticatedError("invalid token", code="invalid_token")
        # Supabase identity is full access here; scopes are not modelled on their side.
        request.state.user_id = str(user.id)
        return CurrentUser(user.id, user.email, user.is_admin, frozenset({"read", "write"}), "supabase")

    try:
        user_id, token_version = decode_access_token(token)
    except jwt.InvalidTokenError as exc:
        raise UnauthenticatedError("invalid token", code="invalid_token") from exc
    user = db.get(User, user_id)
    if user is None:
        raise UnauthenticatedError("unknown user", code="invalid_token")
    if token_version != user.token_version:
        # Issued before logout-all (or a password change); the user must sign in again.
        raise UnauthenticatedError("token has been revoked", code="invalid_token")
    # A JWT is a full-privilege credential; scopes only restrict tokens.
    request.state.user_id = str(user.id)
    return CurrentUser(user.id, user.email, user.is_admin, frozenset({"read", "write"}), "jwt")


def require_scope(scope: str) -> Callable[..., CurrentUser]:
    def dep(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if not user.has(scope):
            raise PermissionDeniedError(f"token is missing the '{scope}' scope", code="insufficient_scope")
        return user

    return dep


def require_admin(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not user.is_admin:
        raise PermissionDeniedError("administrator privileges required", code="admin_required")
    return user


def require_admin_write(user: CurrentUser = Depends(require_admin)) -> CurrentUser:
    if not user.has("write"):
        raise PermissionDeniedError("token is missing the 'write' scope", code="insufficient_scope")
    return user


class Pagination:
    def __init__(
        self,
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
    ):
        self.limit = limit
        self.offset = offset


@contextmanager
def session_scope():
    """A session for code that runs outside a request (SSE generators, jobs)."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


__all__ = [
    "PAT_PREFIX",
    "CurrentUser",
    "Pagination",
    "get_current_user",
    "get_db",
    "get_session_factory",
    "require_admin",
    "require_admin_write",
    "require_scope",
    "session_scope",
]
