"""K5.4: `topic_id` on GET /v1/conflicts and `from`/`to` aliases on GET /v1/timeline."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.security import create_access_token, hash_password
from app.main import create_app
from app.models.claim import ClaimCluster, ClaimRelation
from app.models.user import User
from app.models.wiki import Topic

pytestmark = pytest.mark.db


@pytest.fixture(scope="module")
def topical_ledger(engine, truncate_after_module):
    """The demo corpus with its clusters split across two topics."""
    from app.core.config import get_settings
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
                email=f"k54{uuid.uuid4().hex[:10]}@test.local",
                password_hash=hash_password("k54-password-123"),
            )
        )
        session.commit()
        seed_demo(session, user_id)
        session.commit()
        drain(session)
        session.commit()

        clusters = sorted(
            session.execute(select(ClaimCluster).where(ClaimCluster.user_id == user_id)).scalars(),
            key=lambda c: c.id,
        )
        assert len(clusters) >= 2, "the demo corpus must produce several clusters"
        topic_a = Topic(user_id=user_id, slug="k54-a", label="Topic A")
        topic_b = Topic(user_id=user_id, slug="k54-b", label="Topic B")
        session.add_all([topic_a, topic_b])
        session.flush()
        half = len(clusters) // 2
        for index, cluster in enumerate(clusters):
            cluster.topic_id = topic_a.id if index < half else topic_b.id
        session.commit()
        yield session, user_id, topic_a.id
    finally:
        session.rollback()
        settings.jobs_inline = original
        session.close()


@pytest.fixture(scope="module")
def topical_client(topical_ledger):
    session, user_id, _ = topical_ledger
    app = create_app()
    app.dependency_overrides[get_db] = lambda: session
    with TestClient(app) as client:
        client.headers.update({"Authorization": f"Bearer {create_access_token(user_id)}"})
        yield client
    app.dependency_overrides.clear()


def _expected_conflict_ids(session: Session, user_id: uuid.UUID, topic_id: int | None) -> set[int]:
    relations = session.execute(
        select(ClaimRelation).where(
            ClaimRelation.user_id == user_id,
            ClaimRelation.relation == "contradicts",
            ClaimRelation.resolved_at.is_(None),
        )
    ).scalars()
    topic_cluster_ids = (
        set(
            session.execute(
                select(ClaimCluster.id).where(
                    ClaimCluster.user_id == user_id, ClaimCluster.topic_id == topic_id
                )
            ).scalars()
        )
        if topic_id is not None
        else None
    )
    from app.models.claim import Claim

    out: set[int] = set()
    for relation in relations:
        if relation.user_label == "rejected":
            continue
        if topic_cluster_ids is not None:
            a = session.get(Claim, relation.a_claim_id)
            b = session.get(Claim, relation.b_claim_id)
            if a.cluster_id not in topic_cluster_ids and b.cluster_id not in topic_cluster_ids:
                continue
        out.add(relation.id)
    return out


def test_conflicts_filtered_by_topic_return_only_that_topics_conflicts(topical_client, topical_ledger):
    session, user_id, topic_a = topical_ledger
    everything = topical_client.get("/v1/conflicts", params={"limit": 200})
    assert everything.status_code == 200, everything.text
    all_ids = {row["id"] for row in everything.json()}

    filtered = topical_client.get("/v1/conflicts", params={"topic_id": topic_a, "limit": 200})
    assert filtered.status_code == 200, filtered.text
    got = {row["id"] for row in filtered.json()}

    expected = _expected_conflict_ids(session, user_id, topic_a)
    assert expected, "the split must leave at least one conflict in topic A"
    assert got == expected
    assert got < all_ids, "filtering by one topic must drop some conflicts"


def test_conflicts_topic_filter_rejects_another_users_topic(topical_client, topical_ledger):
    session, _, _ = topical_ledger
    other_id = uuid.uuid4()
    session.add(User(id=other_id, email=f"k54o{uuid.uuid4().hex[:8]}@test.local", password_hash="x"))
    session.flush()
    other_topic = Topic(user_id=other_id, slug=f"k54-x{uuid.uuid4().hex[:6]}", label="Not yours")
    session.add(other_topic)
    session.commit()
    resp = topical_client.get("/v1/conflicts", params={"topic_id": other_topic.id})
    assert resp.status_code == 404, resp.text


def test_timeline_from_to_match_start_end(topical_client):
    full = topical_client.get("/v1/timeline", params={"limit": 200, "axis": "world"})
    assert full.status_code == 200, full.text
    times = sorted(entry["at"] for entry in full.json())
    assert len(times) >= 3, "the demo timeline must have several events"
    start, end = times[len(times) // 3], times[2 * len(times) // 3]

    canonical = topical_client.get("/v1/timeline", params={"start": start, "end": end, "limit": 200})
    aliased = topical_client.get("/v1/timeline", params={"from": start, "to": end, "limit": 200})
    assert canonical.status_code == 200 and aliased.status_code == 200
    assert aliased.json() == canonical.json()
    assert 0 < len(canonical.json()) < len(times), "the window must narrow the timeline"


def test_timeline_start_end_win_over_from_to(topical_client):
    full = topical_client.get("/v1/timeline", params={"limit": 200}).json()
    times = sorted(entry["at"] for entry in full)
    start, end = times[len(times) // 3], times[2 * len(times) // 3]

    canonical = topical_client.get("/v1/timeline", params={"start": start, "end": end, "limit": 200})
    mixed = topical_client.get(
        "/v1/timeline",
        params={"start": start, "end": end, "from": times[0], "to": times[1], "limit": 200},
    )
    assert mixed.json() == canonical.json()
