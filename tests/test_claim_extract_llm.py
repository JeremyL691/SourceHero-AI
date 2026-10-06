"""H4.4 (LLM-3): Acceptance tests for LLM claim extraction path.

Tests:
- Normal parse: LLM returns valid structured claims, saved with extractor='llm(...)'.
- Repair: LLM returns invalid JSON first, repair succeeds and extracts claims.
- Fallback: LLM failure or unhandled error falls back to heuristic extraction.
- Fabricated quote: quote not found in source text is dropped and recorded in rejected_unaligned.
"""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import select

from app.claims.extract import extract_version_claims
from app.core import time as clock
from app.llm.providers.fake import get_fake_provider
from app.models.chunk import Chunk
from app.models.claim import Claim
from app.models.document import Document, DocumentVersion
from app.models.source import Source
from app.models.user import User

pytestmark = pytest.mark.db


def _setup_version(db):
    user = User(
        email=f"u_{uuid.uuid4().hex[:8]}@test.local",
        password_hash="argon2...",
    )
    db.add(user)
    db.flush()

    source = Source(
        user_id=user.id,
        name="Source A",
        source_type="webpage",
        url=f"https://test.local/{uuid.uuid4().hex[:8]}",
    )
    db.add(source)
    db.flush()

    doc = Document(
        user_id=user.id,
        source_id=source.id,
        title="Test Battery Report",
        identity_key=f"url:https://test.local/{uuid.uuid4().hex[:8]}",
    )
    db.add(doc)
    db.flush()

    text = "Halcyon Cells delivered 450 Wh/kg at launch. The company plans expansion next year."
    now = clock.utcnow()
    version = DocumentVersion(
        document_id=doc.id,
        user_id=user.id,
        version_no=1,
        clean_text=text,
        content_hash=uuid.uuid4().hex,
        effective_at=now,
    )
    db.add(version)
    db.flush()

    chunk = Chunk(
        user_id=user.id,
        document_id=doc.id,
        version_id=version.id,
        chunk_index=0,
        chunk_hash=uuid.uuid4().hex,
        text=text,
        char_start=0,
        char_end=len(text),
    )
    db.add(chunk)
    db.flush()
    return version, chunk


def test_llm_claim_extract_success(db):
    version, chunk = _setup_version(db)
    provider = get_fake_provider()
    payload = {
        "claims": [
            {
                "statement": "Halcyon Cells delivered 450 Wh/kg at launch.",
                "quote": "Halcyon Cells delivered 450 Wh/kg at launch.",
                "claim_type": "number",
                "subject": "Halcyon Cells",
                "numbers": [{"value": 450.0, "unit": "Wh/kg", "text": "450 Wh/kg"}],
                "confidence": 0.9,
            }
        ]
    }
    provider.script("claim_extract", [json.dumps(payload)])

    stats = extract_version_claims(db, version.id)
    assert stats.created == 1
    assert stats.rejected_unaligned == 0

    claim = db.execute(select(Claim).where(Claim.version_id == version.id)).scalar_one()
    assert claim.statement == "Halcyon Cells delivered 450 Wh/kg at launch."
    assert "llm" in claim.extractor


def test_llm_claim_extract_repair(db):
    version, chunk = _setup_version(db)
    provider = get_fake_provider()
    payload = {
        "claims": [
            {
                "statement": "Halcyon Cells delivered 450 Wh/kg at launch.",
                "quote": "Halcyon Cells delivered 450 Wh/kg at launch.",
                "claim_type": "number",
                "subject": "Halcyon Cells",
                "numbers": [{"value": 450.0, "unit": "Wh/kg", "text": "450 Wh/kg"}],
                "confidence": 0.85,
            }
        ]
    }
    # First response is malformed JSON; second response is valid JSON repaired
    provider.script("claim_extract", ["invalid json text here", json.dumps(payload)])

    stats = extract_version_claims(db, version.id)
    assert stats.created == 1

    claim = db.execute(select(Claim).where(Claim.version_id == version.id)).scalar_one()
    assert claim.statement == "Halcyon Cells delivered 450 Wh/kg at launch."


def test_llm_claim_extract_fallback(db, monkeypatch):
    version, chunk = _setup_version(db)

    # Monkeypatch LLMClient.complete to simulate provider error
    from app.llm.client import LLMClient
    from app.llm.types import LLMUnavailable

    def _fail_complete(*args, **kwargs):
        raise LLMUnavailable("test upstream outage")

    monkeypatch.setattr(LLMClient, "complete", _fail_complete)

    # Should gracefully fall back to heuristic extraction without failing
    stats = extract_version_claims(db, version.id)
    assert stats.created >= 1

    claim = db.execute(select(Claim).where(Claim.version_id == version.id)).scalars().first()
    assert claim.extractor == "heuristic"


def test_llm_claim_extract_fabricated_quote_dropped(db):
    version, chunk = _setup_version(db)
    provider = get_fake_provider()
    payload = {
        "claims": [
            {
                "statement": "Fabricated claim about alien batteries.",
                "quote": "This quote is nowhere to be found in the passage.",
                "claim_type": "fact",
                "subject": "Aliens",
                "numbers": [],
                "confidence": 0.5,
            }
        ]
    }
    provider.script("claim_extract", [json.dumps(payload)])

    stats = extract_version_claims(db, version.id)
    # Fabricated quote must be rejected and not stored
    assert stats.created == 0
    assert stats.rejected_unaligned == 1
    assert db.execute(select(Claim).where(Claim.version_id == version.id)).first() is None
