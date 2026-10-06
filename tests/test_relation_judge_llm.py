"""H4.5 (LLM-3): Acceptance tests for LLM relation judging path.

Tests:
- Normal judgment: deferred pair sent to LLM, classified as contradicts/supports with method='llm'.
- Repair: malformed JSON on first try repaired on retry.
- Fallback: LLM failure keeps pairs in deferred without failing.
- Deferred count drops: LLM resolving pairs reduces deferred count.
"""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import select

from app.claims.relate import judge_relations
from app.core import time as clock
from app.indexing.embeddings import get_embedder
from app.llm.providers.fake import get_fake_provider
from app.models.claim import Claim, ClaimRelation
from app.models.document import Document, DocumentVersion
from app.models.source import Source
from app.models.user import User

pytestmark = pytest.mark.db


def _setup_claims_for_relation(db):
    user = User(
        email=f"u_{uuid.uuid4().hex[:8]}@test.local",
        password_hash="argon2...",
    )
    db.add(user)
    db.flush()

    s1 = Source(user_id=user.id, name="Src 1", source_type="webpage", url="https://t1.test")
    s2 = Source(user_id=user.id, name="Src 2", source_type="webpage", url="https://t2.test")
    db.add_all([s1, s2])
    db.flush()

    d1 = Document(user_id=user.id, source_id=s1.id, identity_key="url:https://t1.test", title="Doc 1")
    d2 = Document(user_id=user.id, source_id=s2.id, identity_key="url:https://t2.test", title="Doc 2")
    db.add_all([d1, d2])
    db.flush()

    now = clock.utcnow()
    v1 = DocumentVersion(
        document_id=d1.id,
        user_id=user.id,
        version_no=1,
        clean_text="t1",
        content_hash="h1",
        effective_at=now,
    )
    v2 = DocumentVersion(
        document_id=d2.id,
        user_id=user.id,
        version_no=1,
        clean_text="t2",
        content_hash="h2",
        effective_at=now,
    )
    db.add_all([v1, v2])
    db.flush()

    embedder = get_embedder()
    vec1 = embedder.embed_query("First project investigated energy storage systems in facility alpha.")
    n = len(vec1)
    vec2 = [0.6 * vec1[i] + 0.8 * (-vec1[(i + n // 2) % n]) for i in range(n)]

    stmt1 = "First project investigated energy storage systems in facility alpha."
    stmt2 = "Second team tested prototype components within testing site beta."

    c1 = Claim(
        user_id=user.id,
        document_id=d1.id,
        version_id=v1.id,
        statement=stmt1,
        quote=stmt1,
        char_start=0,
        char_end=len(stmt1),
        asserted_at=now,
        observed_at=now,
        status="active",
        claim_hash=uuid.uuid4().hex,
        extractor="heuristic",
        embedding=vec1,
    )
    c2 = Claim(
        user_id=user.id,
        document_id=d2.id,
        version_id=v2.id,
        statement=stmt2,
        quote=stmt2,
        char_start=0,
        char_end=len(stmt2),
        asserted_at=now,
        observed_at=now,
        status="active",
        claim_hash=uuid.uuid4().hex,
        extractor="heuristic",
        embedding=vec2,
    )
    db.add_all([c1, c2])
    db.flush()
    return user, c1, c2


def test_llm_relation_judge_success(db):
    user, c1, c2 = _setup_claims_for_relation(db)
    provider = get_fake_provider()
    payload = {
        "results": [
            {
                "pair_index": 1,
                "relation": "contradicts",
                "direction": None,
                "confidence": 0.82,
                "rationale": "Different facilities claimed for testing",
            }
        ]
    }
    provider.script("relation_judge", [json.dumps(payload)])

    stats = judge_relations(db, [c1.id, c2.id])
    assert stats["contradicts"] == 1
    assert stats["deferred"] == 0
    assert stats["pairs"] == 1

    rel = db.execute(select(ClaimRelation).where(ClaimRelation.user_id == user.id)).scalar_one()
    assert rel.relation == "contradicts"
    assert rel.method == "llm"


def test_llm_relation_judge_repair(db):
    user, c1, c2 = _setup_claims_for_relation(db)
    provider = get_fake_provider()
    payload = {
        "results": [
            {
                "pair_index": 1,
                "relation": "supports",
                "direction": None,
                "confidence": 0.75,
                "rationale": "Compatible research efforts",
            }
        ]
    }
    # Script invalid json first, valid repaired JSON second
    provider.script("relation_judge", ["broken json", json.dumps(payload)])

    stats = judge_relations(db, [c1.id, c2.id])
    assert stats["supports"] == 1
    assert stats["deferred"] == 0

    rel = db.execute(select(ClaimRelation).where(ClaimRelation.user_id == user.id)).scalar_one()
    assert rel.relation == "supports"
    assert rel.method == "llm"


def test_llm_relation_judge_fallback(db, monkeypatch):
    user, c1, c2 = _setup_claims_for_relation(db)

    from app.llm.client import LLMClient
    from app.llm.types import LLMUnavailable

    def _fail_complete(*args, **kwargs):
        raise LLMUnavailable("model server down")

    monkeypatch.setattr(LLMClient, "complete", _fail_complete)

    stats = judge_relations(db, [c1.id, c2.id])
    # The pair could not be judged by LLM, so it remains deferred
    assert stats["deferred"] >= 1


def test_llm_relation_judge_deferred_drops(db):
    user, c1, c2 = _setup_claims_for_relation(db)

    # In default fake mode with no script, fake default returns "unrelated"
    stats = judge_relations(db, [c1.id, c2.id])
    # The deferred count dropped from 1 to 0 because LLM settled it as unrelated
    assert stats["deferred"] == 0
