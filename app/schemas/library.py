"""Collections, tags and document flags."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class CollectionIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None


class CollectionPatchIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None


class CollectionOut(BaseModel):
    id: int
    name: str
    description: str | None = None
    document_count: int = 0
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class CollectionItemIn(BaseModel):
    document_id: uuid.UUID


class TagIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class TagOut(BaseModel):
    id: int
    name: str
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class TagRef(BaseModel):
    id: int
    name: str


class CollectionRef(BaseModel):
    id: int
    name: str


class DocumentTagIn(BaseModel):
    tag_id: int


class DocumentFlagsIn(BaseModel):
    starred: bool | None = None
    archived: bool | None = None


class DocumentFlagsOut(BaseModel):
    document_id: uuid.UUID
    starred: bool = False
    archived: bool = False


__all__ = [
    "CollectionIn",
    "CollectionItemIn",
    "CollectionOut",
    "CollectionPatchIn",
    "CollectionRef",
    "DocumentFlagsIn",
    "DocumentFlagsOut",
    "DocumentTagIn",
    "TagIn",
    "TagOut",
    "TagRef",
]
