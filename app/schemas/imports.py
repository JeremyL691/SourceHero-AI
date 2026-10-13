"""Import result schemas."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ImportErrorOut(BaseModel):
    code: str
    url: str | None = None
    row: int | None = None


class ImportResultOut(BaseModel):
    created: int = Field(ge=0)
    skipped: int = Field(ge=0)
    errors: list[ImportErrorOut] = Field(default_factory=list)


__all__ = ["ImportErrorOut", "ImportResultOut"]
