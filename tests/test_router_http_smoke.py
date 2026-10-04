"""HTTP smoke tests for routes that previously had no HTTP coverage (TEST-1)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from httpx import Response

from app.models.answer import Answer
from app.models.claim import Claim, ClaimCluster
from app.models.document import Document, DocumentVersion
from app.models.source import Source
from app.models.wiki import Topic, WikiPage

pytestmark = pytest.mark.db


def _user_id(client) -> uuid.UUID:
    resp = client.get("/v1/auth/me")
    assert resp.status_code == 200
    return uuid.UUID(resp.json()["id"])


def test_claims_routes_http(make_client, db):
    c = make_client(email=f"claims_{uuid.uuid4().hex[:8]}@test.local")
    user_id = _user_id(c)

    source = Source(user_id=user_id, name="Test Source", source_type="webpage", url="https://example.com/src")
    db.add(source)
    db.flush()

    doc = Document(
        user_id=user_id, source_id=source.id, title="Test Doc", identity_key=f"url:{uuid.uuid4().hex}"
    )
    db.add(doc)
    db.flush()

    ver = DocumentVersion(
        user_id=user_id,
        document_id=doc.id,
        version_no=1,
        raw_text="Test content",
        clean_text="Test content",
        content_hash="hash1",
        effective_at=datetime.now(UTC),
    )
    db.add(ver)
    db.flush()

    cluster = ClaimCluster(user_id=user_id, label="battery", size=1, conflict_count=0)
    db.add(cluster)
    db.flush()

    claim = Claim(
        user_id=user_id,
        document_id=doc.id,
        version_id=ver.id,
        cluster_id=cluster.id,
        claim_hash=f"chash_{uuid.uuid4().hex}",
        statement="Test battery statement",
        quote="Test content",
        status="active",
        asserted_at=datetime.now(UTC),
        observed_at=datetime.now(UTC),
        char_start=0,
        char_end=12,
    )
    db.add(claim)
    db.commit()

    resp = c.get("/v1/claims")
    assert resp.status_code == 200
    assert len(resp.json()) >= 1
    assert resp.json()[0]["statement"] == "Test battery statement"

    resp = c.get(f"/v1/claims/{claim.id}")
    assert resp.status_code == 200
    assert resp.json()["statement"] == "Test battery statement"

    resp = c.get("/v1/clusters")
    assert resp.status_code == 200
    assert len(resp.json()) >= 1

    resp = c.get(f"/v1/clusters/{cluster.id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == cluster.id

    resp = c.get("/v1/conflicts")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)

    resp = c.get(f"/v1/documents/{doc.id}/claims")
    assert resp.status_code == 200
    assert len(resp.json()) >= 1


def test_wiki_routes_http(make_client, db):
    c = make_client(email=f"wiki_{uuid.uuid4().hex[:8]}@test.local")
    user_id = _user_id(c)

    topic = Topic(user_id=user_id, slug="energy-density", label="Energy Density")
    db.add(topic)
    db.flush()

    page = WikiPage(
        user_id=user_id,
        topic_id=topic.id,
        slug="energy-density",
        title="Energy Density",
        body_md="# Energy Density\nOverview content.",
        input_hash="hash1",
        revision=1,
    )
    db.add(page)
    db.commit()

    resp = c.get("/v1/topics")
    assert resp.status_code == 200
    assert any(t["slug"] == "energy-density" for t in resp.json())

    resp = c.get("/v1/wiki/pages")
    assert resp.status_code == 200
    assert any(p["slug"] == "energy-density" for p in resp.json())

    resp = c.get("/v1/wiki/pages/energy-density")
    assert resp.status_code == 200
    assert resp.json()["slug"] == "energy-density"
    assert "Overview content" in resp.json()["body_md"]

    resp = c.get("/v1/wiki/pages/energy-density/revisions")
    assert resp.status_code == 200

    resp = c.get("/v1/wiki/lint")
    assert resp.status_code == 200

    resp = c.get("/v1/wiki/export")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"


def test_temporal_routes_http(make_client, db):
    c = make_client(email=f"temporal_{uuid.uuid4().hex[:8]}@test.local")

    resp = c.get("/v1/watches")
    assert resp.status_code == 200
    assert resp.json() == []

    resp = c.post("/v1/watches", json={"kind": "query", "query": "solid state"})
    assert resp.status_code == 200
    watch_id = resp.json()["id"]

    resp = c.get("/v1/watches")
    assert resp.status_code == 200
    assert len(resp.json()) == 1

    resp = c.get("/v1/timeline")
    assert resp.status_code == 200

    resp = c.get("/v1/stale-notes")
    assert resp.status_code == 200

    resp = c.get("/v1/diffs")
    assert resp.status_code == 200

    resp = c.delete(f"/v1/watches/{watch_id}")
    assert resp.status_code == 204


def test_answers_routes_http(make_client, db):
    c = make_client(email=f"answers_{uuid.uuid4().hex[:8]}@test.local")
    user_id = _user_id(c)

    answer = Answer(
        user_id=user_id,
        question="What is the density?",
        answer_md="The density is 450 Wh/kg.",
        sentences=[
            {
                "index": 0,
                "text": "The density is 450 Wh/kg.",
                "char_start": 0,
                "char_end": 26,
                "verdict": "supported",
            }
        ],
        citations=[],
        retrieval={},
        method="heuristic",
    )
    db.add(answer)
    db.commit()

    resp = c.get("/v1/answers")
    assert resp.status_code == 200
    assert any(a["id"] == str(answer.id) for a in resp.json())

    resp = c.get(f"/v1/answers/{answer.id}")
    assert resp.status_code == 200
    assert resp.json()["question"] == "What is the density?"

    resp = c.post(f"/v1/answers/{answer.id}/save-note", json={"title": "Density Note"})
    assert resp.status_code == 200
    assert resp.json()["source_id"]


def test_captures_routes_http(make_client, respx_mock):
    c = make_client(email=f"captures_{uuid.uuid4().hex[:8]}@test.local")

    respx_mock.get("https://news.example.com/item").mock(
        return_value=Response(200, html="<html><title>News</title><body>Article content here</body></html>")
    )

    resp = c.post("/v1/captures/parse", json={"url": "https://news.example.com/item"})
    assert resp.status_code == 200
    assert resp.json()["title"] == "News"

    resp = c.post("/v1/captures", json={"text": "Interesting excerpt note from user"})
    assert resp.status_code == 201
    assert resp.json()["job_id"]
