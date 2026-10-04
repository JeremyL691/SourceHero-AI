"""Passwords and JWTs. Nothing here reads a credential store -- the secret comes from
`SOURCEHERO_SECRET_KEY`, and tests pass an explicit key.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from app.core.config import get_settings
from app.core.time import utcnow

_hasher = PasswordHasher()


# --- passwords ----------------------------------------------------------


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    if not password_hash:
        return False
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, ValueError):
        return False


# --- JWT ----------------------------------------------------------------


def create_access_token(
    user_id: uuid.UUID, *, secret: str | None = None, ttl_hours: int | None = None
) -> str:
    settings = get_settings()
    now = utcnow()
    payload = {
        "sub": str(user_id),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=ttl_hours or settings.jwt_ttl_hours)).timestamp()),
        "typ": "access",
    }
    return jwt.encode(payload, secret or settings.jwt_secret, algorithm="HS256")


def decode_access_token(token: str, *, secret: str | None = None) -> uuid.UUID:
    settings = get_settings()
    payload = jwt.decode(
        token, secret or settings.jwt_secret, algorithms=["HS256"], options={"require": ["sub", "exp"]}
    )
    if payload.get("typ") != "access":
        raise jwt.InvalidTokenError("wrong token type")
    return uuid.UUID(payload["sub"])


__all__ = ["create_access_token", "decode_access_token", "hash_password", "verify_password"]
