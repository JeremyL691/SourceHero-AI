"""Pydantic request/response models for authentication and tokens."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.models.user import SCOPES_READ, SCOPES_WRITE


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    display_name: str | None = None
    is_admin: bool
    auth_provider: str = "local"
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class RegisterIn(BaseModel):
    email: str
    password: str = Field(min_length=10, max_length=200)
    display_name: str | None = Field(default=None, max_length=200)

    @field_validator("email")
    @classmethod
    def _lower(cls, v: str) -> str:
        return v.strip().lower()

    @field_validator("display_name")
    @classmethod
    def _strip(cls, v: str | None) -> str | None:
        return v.strip() if v else None


class LoginIn(BaseModel):
    email: str
    password: str

    @field_validator("email")
    @classmethod
    def _lower(cls, v: str) -> str:
        return v.strip().lower()


class SessionOut(BaseModel):
    access_token: str
    user: UserOut


class TokenCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    scopes: list[str] = Field(min_length=1, default_factory=lambda: [SCOPES_READ, SCOPES_WRITE])

    @field_validator("scopes")
    @classmethod
    def _known(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("scopes cannot be empty")
        allowed = {SCOPES_READ, SCOPES_WRITE}
        bad = set(v) - allowed
        if bad:
            raise ValueError(f"unknown scopes: {', '.join(sorted(bad))}")
        return sorted(set(v))


class TokenOut(BaseModel):
    """The plaintext `token` is present exactly once, in the create response."""

    id: uuid.UUID
    name: str
    prefix: str
    scopes: list[str]
    created_at: datetime | None = None
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None
    token: str | None = None

    model_config = {"from_attributes": True}


__all__ = [
    "LoginIn",
    "RegisterIn",
    "SessionOut",
    "TokenCreateIn",
    "TokenOut",
    "UserOut",
]
