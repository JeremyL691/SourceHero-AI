"""Local accounts and personal access tokens."""

from __future__ import annotations

import hashlib
import secrets
import time
import uuid
from collections import OrderedDict

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
    UnauthenticatedError,
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
_DUMMY_HASH = hash_password("dummy-password-for-timing-equalization")


class RateLimiter:
    """In-memory rate limiter with bounded LRU size and sliding TTL window."""

    def __init__(
        self,
        max_attempts: int = LOGIN_LIMIT,
        window_seconds: float = LOGIN_WINDOW_SECONDS,
        max_keys: int = 10000,
    ) -> None:
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self.max_keys = max_keys
        self.buckets: OrderedDict[str, list[float]] = OrderedDict()

    def check(self, key: str) -> None:
        now = time.monotonic()
        if key in self.buckets:
            hits = [t for t in self.buckets[key] if now - t < self.window_seconds]
            if len(hits) >= self.max_attempts:
                raise BudgetExceededError("too many attempts; try again in a minute")
            if hits:
                self.buckets[key] = hits
                self.buckets.move_to_end(key)
            else:
                del self.buckets[key]

    def record_failure(self, key: str) -> None:
        now = time.monotonic()
        hits = [t for t in self.buckets.get(key, []) if now - t < self.window_seconds]
        hits.append(now)
        self.buckets[key] = hits
        self.buckets.move_to_end(key)
        while len(self.buckets) > self.max_keys:
            self.buckets.popitem(last=False)


def _get_rate_limiter(request: Request) -> RateLimiter | None:
    settings = get_settings()
    if settings.is_test and not getattr(request.app.state, "login_limit_enabled", False):
        return None
    limiter: RateLimiter | None = getattr(request.app.state, "auth_rate_limiter", None)
    if limiter is None:
        limiter = RateLimiter()
        request.app.state.auth_rate_limiter = limiter
    return limiter


def _client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def get_user_by_email(db: Session, email: str) -> User | None:
    return db.execute(select(User).where(User.email == email.strip().lower())).scalar_one_or_none()


def register_user(db: Session, email: str, password: str, display_name: str | None) -> User:
    settings = get_settings()
    if not settings.allow_registration:
        raise PermissionDeniedError("registration is disabled", code="registration_disabled")
    if get_user_by_email(db, email) is not None:
        raise ConflictError("an account with that email already exists", code="email_taken")

    if db.bind and db.bind.dialect.name == "postgresql":
        from sqlalchemy import text

        db.execute(text("SELECT pg_advisory_xact_lock(hashtext('first-admin'))"))

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
def register(payload: RegisterIn, request: Request, db: Session = Depends(get_db)) -> SessionOut:
    limiter = _get_rate_limiter(request)
    key = _client_key(request)
    if limiter is not None:
        limiter.check(key)
    try:
        user = register_user(db, payload.email, payload.password, payload.display_name)
    except Exception:
        if limiter is not None:
            limiter.record_failure(key)
        raise
    return SessionOut(user=UserOut.model_validate(user), access_token=create_access_token(user.id))


@router.post("/auth/login", response_model=SessionOut, summary="Exchange credentials for a JWT")
def login(payload: LoginIn, request: Request, db: Session = Depends(get_db)) -> SessionOut:
    limiter = _get_rate_limiter(request)
    key = _client_key(request)
    if limiter is not None:
        limiter.check(key)

    user = get_user_by_email(db, payload.email)
    valid = verify_password(payload.password, user.password_hash if user else _DUMMY_HASH)
    if user is None or not valid:
        if limiter is not None:
            limiter.record_failure(key)
        raise UnauthenticatedError("invalid email or password", code="invalid_credentials")
    return SessionOut(access_token=create_access_token(user.id), user=UserOut.model_validate(user))


@router.get("/auth/me", response_model=UserOut, summary="The signed-in user")
def me(user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> UserOut:
    row = db.get(User, user.id)
    if row is None:
        raise UnauthenticatedError("unknown user", code="invalid_token")
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
    if user.via == "pat":
        raise PermissionDeniedError(
            "personal access tokens cannot issue tokens", code="pat_issuance_forbidden"
        )
    if not set(payload.scopes).issubset(user.scopes):
        raise PermissionDeniedError(
            "requested scopes must be a subset of caller scopes", code="insufficient_scope"
        )
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
