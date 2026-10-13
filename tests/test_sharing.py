"""Read-only share links : token once, revoke, expiry, tenant isolation, sanitized snapshot."""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from app.core.config import get_settings
from app.core.db import get_db
from app.main import create_app
from app.models.answer import Answer
from app.models.sharing import ShareLink
from app.models.wiki import Topic, WikiPage

FORBIDDEN_KEYS = {"email", "user_id", "password_hash"}


@pytest.fixture
def new_client(db):
    """Factory: a TestClient on an app that serves the sharing router, registered as a new user."""
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db

    def _make(email: str | None = None) -> tuple[TestClient, dict[str, Any]]:
        client = TestClient(app)
        email = email or f"s{uuid.uuid4().hex[:12]}@test.local"
        resp = client.post("/v1/auth/register", json={"email": email, "password": "test-password-123"})
        assert resp.status_code == 201, resp.text
        client.headers.update({"Authorization": f"Bearer {resp.json()['access_token']}"})
        return client, resp.json()["user"]

    yield _make
    app.dependency_overrides.clear()


def _wiki_page(db, user_id: uuid.UUID, slug: str = "ferro-battery") -> WikiPage:
    topic = Topic(user_id=user_id, slug=slug, label="Ferro battery")
    db.add(topic)
    db.flush()
    page = WikiPage(
        user_id=user_id,
        topic_id=topic.id,
        slug=slug,
        title="Ferro battery",
        status="clean",
        body={
            "title": "Ferro battery",
            "summary": [{"text": "It stores charge.", "claim_ids": []}],
            "sections": [
                {
                    "id": "overview",
                    "heading": "Overview",
                    "kind": "overview",
                    "sentences": [{"text": "Cells are sealed.", "claim_ids": []}],
                }
            ],
            "see_also": [],
        },
    )
    db.add(page)
    db.flush()
    return page


def _answer(db, user_id: uuid.UUID) -> Answer:
    row = Answer(
        user_id=user_id,
        question="Is it safe?",
        answer_md="It is safe [1].",
        sentences=[{"text": "It is safe.", "verdict": "supported", "citations": [1], "flags": []}],
        citations=[
            {
                "n": 1,
                "chunk_id": 7,
                "document_id": str(uuid.uuid4()),
                "version_id": 3,
                "title": "Safety note",
                "url": "https://example.test/safety",
                "source": "example",
                "date": "2026-01-02",
                "quote": "It is safe.",
            }
        ],
        retrieval={"hits": 1},
        method="fake",
    )
    db.add(row)
    db.flush()
    return row


def _walk_keys(node: Any) -> set[str]:
    keys: set[str] = set()
    if isinstance(node, dict):
        for k, v in node.items():
            keys.add(k)
            keys |= _walk_keys(v)
    elif isinstance(node, list):
        for item in node:
            keys |= _walk_keys(item)
    return keys


def test_token_is_returned_once_and_never_listed(db, new_client):
    client, user = new_client()
    _wiki_page(db, uuid.UUID(user["id"]))

    created = client.post("/v1/shares", json={"kind": "wiki_page", "target_id": "ferro-battery"})
    assert created.status_code == 201, created.text
    token = created.json()["token"]
    assert len(token) >= 40
    # Only the digest is stored.
    stored = db.execute(select(ShareLink).where(ShareLink.id == created.json()["id"])).scalar_one()
    assert stored.token_hash == hashlib.sha256(token.encode()).hexdigest()
    assert stored.token_hash != token

    listed = client.get("/v1/shares")
    assert listed.status_code == 200, listed.text
    assert len(listed.json()) == 1
    assert "token" not in listed.json()[0]
    assert token not in listed.text


def test_revoked_share_returns_404_on_public_read(db, new_client):
    client, user = new_client()
    _wiki_page(db, uuid.UUID(user["id"]))
    created = client.post("/v1/shares", json={"kind": "wiki_page", "target_id": "ferro-battery"}).json()

    assert client.get(f"/v1/public/shares/{created['token']}").status_code == 200
    revoked = client.delete(f"/v1/shares/{created['id']}")
    assert revoked.status_code == 204, revoked.text
    assert client.get(f"/v1/public/shares/{created['token']}").status_code == 404


def test_expired_share_returns_404_on_public_read(db, new_client):
    client, user = new_client()
    _wiki_page(db, uuid.UUID(user["id"]))
    created = client.post(
        "/v1/shares", json={"kind": "wiki_page", "target_id": "ferro-battery", "expires_in_days": 1}
    ).json()
    assert client.get(f"/v1/public/shares/{created['token']}").status_code == 200

    past = datetime.now(UTC) - timedelta(days=1)
    db.execute(update(ShareLink).where(ShareLink.id == created["id"]).values(expires_at=past))
    db.flush()
    assert client.get(f"/v1/public/shares/{created['token']}").status_code == 404


def test_other_tenant_cannot_revoke(db, new_client):
    owner, owner_user = new_client()
    intruder, _ = new_client()
    _wiki_page(db, uuid.UUID(owner_user["id"]))
    created = owner.post("/v1/shares", json={"kind": "wiki_page", "target_id": "ferro-battery"}).json()

    assert intruder.delete(f"/v1/shares/{created['id']}").status_code == 404
    assert intruder.get("/v1/shares").json() == []
    # The owner's link still works.
    assert owner.get(f"/v1/public/shares/{created['token']}").status_code == 200


def test_public_snapshot_has_no_owner_fields(db, new_client):
    client, user = new_client()
    page = _wiki_page(db, uuid.UUID(user["id"]))
    answer = _answer(db, uuid.UUID(user["id"]))

    wiki_token = client.post("/v1/shares", json={"kind": "wiki_page", "target_id": "ferro-battery"}).json()[
        "token"
    ]
    answer_token = client.post("/v1/shares", json={"kind": "answer", "target_id": str(answer.id)}).json()[
        "token"
    ]

    wiki_body = client.get(f"/v1/public/shares/{wiki_token}").json()
    answer_body = client.get(f"/v1/public/shares/{answer_token}").json()

    assert wiki_body["title"] == page.title
    assert wiki_body["sections"][0]["sentences"][0]["text"] == "Cells are sealed."
    assert answer_body["question"] == "Is it safe?"
    assert answer_body["citations"][0]["url"] == "https://example.test/safety"
    assert answer_body["sentences"][0]["verdict"] == "supported"
    for body in (wiki_body, answer_body):
        assert not (_walk_keys(body) & FORBIDDEN_KEYS)
        assert user["id"] not in str(body)
        assert user["email"] not in str(body)


def test_disabled_sharing_returns_404_on_every_route(db, new_client, monkeypatch):
    client, user = new_client()
    _wiki_page(db, uuid.UUID(user["id"]))
    created = client.post("/v1/shares", json={"kind": "wiki_page", "target_id": "ferro-battery"}).json()

    monkeypatch.setattr(get_settings(), "sharing_enabled", False)
    assert (
        client.post("/v1/shares", json={"kind": "wiki_page", "target_id": "ferro-battery"}).status_code == 404
    )
    assert client.get("/v1/shares").status_code == 404
    assert client.delete(f"/v1/shares/{created['id']}").status_code == 404
    assert client.get(f"/v1/public/shares/{created['token']}").status_code == 404
