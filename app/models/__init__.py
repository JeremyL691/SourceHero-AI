"""All models, imported here so Alembic's autogenerate and app startup see them."""

from __future__ import annotations

from app.models.answer import Answer, Verification
from app.models.base import Base, TimestampMixin, UserMixin
from app.models.chunk import Chunk, IngestionRun
from app.models.claim import Claim, ClaimCluster, ClaimEvent, ClaimRelation
from app.models.document import Document, DocumentVersion
from app.models.eval import EvalRun
from app.models.job import Job, ScheduledJob, ScheduledJobRun
from app.models.library import Collection, CollectionItem, DocumentFlag, ItemTag, Tag
from app.models.llm import LLMCache, LLMRun, OAuthState, ProviderCredential
from app.models.ratelimit import RateLimit
from app.models.source import Source
from app.models.temporal import BeliefDiff, Watch
from app.models.user import ApiToken, AppSetting, User
from app.models.wiki import LintFinding, Topic, WikiLink, WikiPage, WikiPageClaim, WikiPageRevision

__all__ = [
    "Answer",
    "ApiToken",
    "BeliefDiff",
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
    "EvalRun",
    "IngestionRun",
    "ItemTag",
    "ProviderCredential",
    "RateLimit",
    "Job",
    "LintFinding",
    "LLMCache",
    "LLMRun",
    "OAuthState",
    "ScheduledJob",
    "ScheduledJobRun",
    "Source",
    "Tag",
    "Topic",
    "TimestampMixin",
    "User",
    "UserMixin",
    "Verification",
    "Watch",
    "WikiLink",
    "WikiPage",
    "WikiPageClaim",
    "WikiPageRevision",
]
