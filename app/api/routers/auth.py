"""Local accounts and personal access tokens."""

from __future__ import annotations

import hashlib
import secrets
import time
import uuid

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, get_current_user, get_db, require_scope
from app.core.config import get_settings
from app.core.errors import (
    BudgetExceededError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
)
from app.core.security import create_access_token, hash_password, verify_password
from app.core.time import utcnow
from app.models.user import ApiToken, User
from app.schemas.auth import (
    LoginIn,
    RegisterIn,
    SessionOut,
    TokenCreateIn,
    TokenOut,
    UserOut,
)

router = APIRouter(tags=["auth"])

PAT_PREFIX = "sh_pat_"
LOGIN_LIMIT = 10
LOGIN_WINDOW_SECONDS = 60.0


def _login_limiter(request: Request) -> dict[str, list[float]] | None:
    """One bucket dict per app instance; None when disabled.

    Disabled in tests by default: test fixtures create many users in a loop and
    would otherwise trip the limit. `tests/test_auth.py` turns it on explicitly.
    """
    settings = get_settings()
    if settings.is_test and not getattr(request.app.state, "login_limit_enabled", False):
        return None
    bucket: dict[str, list[float]] = getattr(request.app.state, "login_attempts", None) or {}
    request.app.state.login_attempts = bucket
    return bucket


def _check_rate_limit(bucket: dict[str, list[float]] | None, request: Request) -> None:
    if bucket is None:
        return
    now = time.monotonic()
    key = request.client.host if request.client else "unknown"
    hits = [t for t in bucket.get(key, []) if now - t < LOGIN_WINDOW_SECONDS]
    if len(hits) >= LOGIN_LIMIT:
        raise BudgetExceededError("too many login attempts; try again in a minute")
    hits.append(now)
    bucket[key] = hits


def get_user_by_email(db: Session, email: str) -> User | None:
    return db.execute(select(User).where(User.email == email.strip().lower())).scalar_one_or_none()


def register_user(db: Session, email: str, password: str, display_name: str | None) -> User:
    settings = get_settings()
    if not settings.allow_registration:
        raise PermissionDeniedError("registration is disabled", code="registration_disabled")
    if get_user_by_email(db, email) is not None:
        raise ConflictError("an account with that email already exists", code="email_taken")

    # The first account owns the instance; afterwards admins are created in Settings.
    is_first = (db.execute(select(func.count()).select_from(User)).scalar_one() or 0) == 0
    user = User(
        email=email.strip().lower(),
        password_hash=hash_password(password),
        display_name=display_name,
        is_admin=is_first,
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError as exc:  # two concurrent registrations of the same email
        db.rollback()
        raise ConflictError("an account with that email already exists", code="email_taken") from exc
    db.refresh(user)
    return user


@router.post(
    "/auth/register",
    response_model=SessionOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a local account",
)
def register(payload: RegisterIn, db: Session = Depends(get_db)) -> SessionOut:
    user = register_user(db, payload.email, payload.password, payload.display_name)
    return SessionOut(user=UserOut.model_validate(user), access_token=create_access_token(user.id))


@router.post("/auth/login", response_model=SessionOut, summary="Exchange credentials for a JWT")
def login(payload: LoginIn, request: Request, db: Session = Depends(get_db)) -> SessionOut:
    bucket = _login_limiter(request)
    _check_rate_limit(bucket, request)

    user = get_user_by_email(db, payload.email)
    # One message for both "no such user" and "wrong password", so the endpoint
    # cannot be used to enumerate accounts.
    if user is None or not verify_password(payload.password, user.password_hash):
        raise PermissionDeniedError("invalid email or password", code="invalid_credentials")
    return SessionOut(access_token=create_access_token(user.id), user=UserOut.model_validate(user))


@router.get("/auth/me", response_model=UserOut, summary="The signed-in user")
def me(user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> UserOut:
    row = db.get(User, user.id)
    if row is None:
        raise PermissionDeniedError("unknown user", code="invalid_token")
    return UserOut.model_validate(row)


# --- personal access tokens --------------------------------------------


def create_pat(db: Session, user_id: uuid.UUID, name: str, scopes: list[str]) -> tuple[ApiToken, str]:
    plaintext = PAT_PREFIX + secrets.token_urlsafe(32)
    row = ApiToken(
        user_id=user_id,
        name=name,
        prefix=plaintext[:12],
        token_hash=hashlib.sha256(plaintext.encode()).hexdigest(),
        scopes=scopes,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row, plaintext


@router.get("/tokens", response_model=list[TokenOut], summary="List your tokens")
def list_tokens(
    user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[TokenOut]:
    rows = (
        db.execute(select(ApiToken).where(ApiToken.user_id == user.id).order_by(ApiToken.created_at.desc()))
        .scalars()
        .all()
    )
    return [TokenOut.model_validate(r) for r in rows]


@router.post(
    "/tokens",
    response_model=TokenOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a token (plaintext returned once)",
)
def create_token(
    payload: TokenCreateIn,
    user: CurrentUser = Depends(require_scope("write")),
    db: Session = Depends(get_db),
) -> TokenOut:
    row, plaintext = create_pat(db, user.id, payload.name, payload.scopes)
    out = TokenOut.model_validate(row)
    return out.model_copy(update={"token": plaintext})


@router.delete("/tokens/{token_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Revoke a token")
def revoke_token(
    token_id: uuid.UUID,
    user: CurrentUser = Depends(require_scope("write")),
    db: Session = Depends(get_db),
) -> None:
    row = db.get(ApiToken, token_id)
    # 404, not 403: a 403 here would confirm that someone else's token id exists.
    if row is None or row.user_id != user.id:
        raise NotFoundError("token not found", code="not_found")
    row.revoked_at = utcnow()
    db.commit()


__all__ = ["create_pat", "register_user", "router"]
