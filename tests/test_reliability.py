"""K8.3: source reliability on PATCH /v1/sources/{id} and weighted camp counts."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from app.models.claim import Claim, ClaimCluster, ClaimRelation
from app.models.document import Document, DocumentVersion
from app.models.source import Source


def _make_source(db, user_id: uuid.UUID, reliability: str = "unknown") -> Source:
    source = Source(
        user_id=user_id, source_type="note", name=f"src-{uuid.uuid4().hex[:6]}", reliability=reliability
    )
    db.add(source)
    db.flush()
    return source


@pytest.mark.db
def test_patch_sets_reliability_and_note_and_get_returns_them(user_factory, db):
    client, me = user_factory()
    source = _make_source(db, uuid.UUID(me["id"]))

    patched = client.patch(
        f"/v1/sources/{source.id}",
        json={"reliability": "high", "reliability_note": "Peer-reviewed lab"},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["reliability"] == "high"
    assert patched.json()["reliability_note"] == "Peer-reviewed lab"

    fetched = client.get(f"/v1/sources/{source.id}")
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["reliability"] == "high"
    assert fetched.json()["reliability_note"] == "Peer-reviewed lab"

    cleared = client.patch(f"/v1/sources/{source.id}", json={"reliability_note": None})
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["reliability"] == "high"
    assert cleared.json()["reliability_note"] is None


@pytest.mark.db
@pytest.mark.parametrize("value", ["certain", "HIGH", "", None])
def test_invalid_reliability_returns_422(user_factory, db, value):
    client, me = user_factory()
    source = _make_source(db, uuid.UUID(me["id"]), reliability="medium")

    resp = client.patch(f"/v1/sources/{source.id}", json={"reliability": value})
    assert resp.status_code == 422, resp.text
    assert "reliability" in resp.text

    db.refresh(source)
    assert source.reliability == "medium"


def _two_claim_cluster(db, user_id: uuid.UUID, first: str, second: str) -> tuple[ClaimCluster, Claim, Claim]:
    """Two claims from two sources that contradict each; the lower id lands in camp A alone."""
    cluster = ClaimCluster(user_id=user_id, label="reliability test", size=2)
    db.add(cluster)
    db.flush()
    claims: list[Claim] = []
    for level in (first, second):
        source = _make_source(db, user_id, reliability=level)
        doc = Document(user_id=user_id, source_id=source.id, identity_key=f"doc:{uuid.uuid4()}")
        db.add(doc)
        db.flush()
        version = DocumentVersion(
            document_id=doc.id,
            user_id=user_id,
            version_no=1,
            content_hash=uuid.uuid4().hex,
            clean_text="The HX-2 cell delivers 450 Wh/kg.",
            effective_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        db.add(version)
        db.flush()
        claim = Claim(
            user_id=user_id,
            document_id=doc.id,
            version_id=version.id,
            statement="The HX-2 cell delivers 450 Wh/kg.",
            quote="The HX-2 cell delivers 450 Wh/kg.",
            char_start=0,
            char_end=33,
            asserted_at=datetime(2026, 1, 1, tzinfo=UTC),
            observed_at=datetime(2026, 1, 1, tzinfo=UTC),
            claim_hash=uuid.uuid4().hex,
            status="active",
            cluster_id=cluster.id,
        )
        db.add(claim)
        db.flush()
        claims.append(claim)
    a, b = claims
    db.add(
        ClaimRelation(
            user_id=user_id,
            a_claim_id=a.id,
            b_claim_id=b.id,
            relation="contradicts",
            confidence=0.9,
        )
    )
    db.flush()
    return cluster, a, b


@pytest.mark.db
@pytest.mark.parametrize(
    ("level", "expected"),
    [("high", 3), ("medium", 2), ("low", 1), ("unknown", 1)],
)
def test_weighted_count_follows_source_reliability(user_factory, db, level, expected):
    client, me = user_factory()
    cluster, first, _second = _two_claim_cluster(db, uuid.UUID(me["id"]), level, "low")

    resp = client.get(f"/v1/clusters/{cluster.id}")
    assert resp.status_code == 200, resp.text
    camps = resp.json()["camps"]["camps"]
    camp = next(c for c in camps if first.id in c["claim_ids"])
    assert camp["claim_ids"] == [first.id]
    assert camp["weighted_count"] == expected


@pytest.mark.db
def test_patch_on_another_tenants_source_returns_404(user_factory, db):
    owner_client, owner = user_factory()
    other_client, _other = user_factory()
    source = _make_source(db, uuid.UUID(owner["id"]), reliability="low")

    resp = other_client.patch(f"/v1/sources/{source.id}", json={"reliability": "high"})
    assert resp.status_code == 404, resp.text

    db.refresh(source)
    assert source.reliability == "low"
