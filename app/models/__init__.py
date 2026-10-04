"""All models, imported here so Alembic's autogenerate and app startup see them."""

from __future__ import annotations

from app.models.answer import Answer, Verification
from app.models.base import Base, TimestampMixin, UserMixin
from app.models.chunk import Chunk, IngestionRun
from app.models.claim import Claim, ClaimCluster, ClaimEvent, ClaimRelation
from app.models.document import Document, DocumentVersion
from app.models.job import Job, ScheduledJob, ScheduledJobRun
from app.models.library import Collection, CollectionItem, DocumentFlag, ItemTag, Tag
from app.models.llm import LLMCache, LLMRun, OAuthState, ProviderCredential
from app.models.source import Source
from app.models.user import ApiToken, AppSetting, User

__all__ = [
    "Answer",
    "ApiToken",
    "AppSetting",
    "Base",
    "Chunk",
    "Claim",
    "ClaimCluster",
    "ClaimEvent",
    "ClaimRelation",
    "Collection",
    "CollectionItem",
    "Document",
    "DocumentFlag",
    "DocumentVersion",
    "IngestionRun",
    "ItemTag",
    "ProviderCredential",
    "Job",
    "LLMCache",
    "LLMRun",
    "OAuthState",
    "ScheduledJob",
    "ScheduledJobRun",
    "Source",
    "Tag",
    "TimestampMixin",
    "User",
    "UserMixin",
    "Verification",
]
