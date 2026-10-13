"""A conflict whose two sides were clustered apart still gets a two-sided divergence map."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.claims.camps import cluster_camp_inputs, compute_camps
from app.models.claim import Claim, ClaimCluster, ClaimRelation
from app.models.user import User

pytestmark = pytest.mark.db


@pytest.fixture(scope="module")
def demo_session(engine, truncate_after_module):
    """The demo corpus drained into clusters, committed so the cluster queries see real rows."""
    from app.core.config import get_settings
    from app.core.security import hash_password
    from app.demo.seed import drain, seed_demo

    settings = get_settings()
    original = settings.jobs_inline
    settings.jobs_inline = False
    session = Session(bind=engine)
    user_id = uuid.uuid4()
    try:
        session.add(
            User(
                id=user_id,
                email=f"split{uuid.uuid4().hex[:10]}@test.local",
                password_hash=hash_password("split-password-123"),
            )
        )
        session.commit()
        seed_demo(session, user_id)
        session.commit()
        drain(session)
        session.commit()
        yield session, user_id
    finally:
        session.rollback()
        settings.jobs_inline = original
        session.close()


def _density_side(statement: str) -> str | None:
    if "380 Wh/kg" in statement:
        return "380"
    if "450 Wh/kg" in statement:
        return "450"
    return None


def test_the_other_side_joins_the_map_when_it_sits_in_another_cluster(demo_session):
    session, user_id = demo_session
    contradictions = session.execute(
        select(ClaimRelation).where(
            ClaimRelation.user_id == user_id,
            ClaimRelation.relation == "contradicts",
            ClaimRelation.resolved_at.is_(None),
        )
    ).scalars()
    pair = next(
        r
        for r in contradictions
        if {
            _density_side(session.get(Claim, r.a_claim_id).statement),
            _density_side(session.get(Claim, r.b_claim_id).statement),
        }
        == {"380", "450"}
    )
    first, second = session.get(Claim, pair.a_claim_id), session.get(Claim, pair.b_claim_id)
    side_450, side_380 = (first, second) if _density_side(first.statement) == "450" else (second, first)
    assert side_450.status == side_380.status == "active"
    assert side_450.cluster_id == side_380.cluster_id, "the demo corpus clusters the two sides together"

    # Cluster the 380 side on its own, as an order-dependent first pass can leave it.
    split = ClaimCluster(user_id=user_id, label="split", label_source="keywords", size=1, doc_count=1)
    session.add(split)
    session.flush()
    side_380.cluster_id = split.id
    session.flush()

    claims, relations = cluster_camp_inputs(session, side_450.cluster_id)
    camps = compute_camps(claims, relations)
    assert side_380.id in {c.id for c in claims}
    assert len(camps.camps) == 2, "both sides of the conflict must be on the map"
