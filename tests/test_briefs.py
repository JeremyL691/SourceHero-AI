"""Evidence briefs: footnote quotes are source slices, tenants are isolated, MCP and format work."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from app.api.deps import CurrentUser
from app.mcp.tools import TOOLS
from app.models.answer import Answer
from app.models.claim import Claim, ClaimCluster
from app.models.document import Document, DocumentVersion
from app.models.source import Source
from app.models.wiki import Topic, WikiPage

pytestmark = pytest.mark.db

SOURCE_TEXT = "Alpha beta. The reactor cooled to 40 degrees. Gamma delta."
SPAN = "The reactor cooled to 40 degrees."


def _document(db, uid: uuid.UUID) -> tuple[Document, DocumentVersion]:
    source = Source(user_id=uid, source_type="url", name="Reactor feed")
    db.add(source)
    db.flush()
    doc = Document(
        user_id=uid,
        source_id=source.id,
        identity_key=f"url:{uuid.uuid4().hex}",
        title="Reactor report",
        url="https://example.test/reactor",
        published_at=datetime(2026, 3, 4, tzinfo=UTC),
    )
    db.add(doc)
    db.flush()
    version = DocumentVersion(
        user_id=uid,
        document_id=doc.id,
        version_no=1,
        content_hash=uuid.uuid4().hex,
        clean_text=SOURCE_TEXT,
        effective_at=datetime.now(UTC),
    )
    db.add(version)
    db.flush()
    return doc, version


def _answer(db, uid: uuid.UUID, doc: Document, version: DocumentVersion) -> Answer:
    start = SOURCE_TEXT.index(SPAN)
    end = start + len(SPAN)
    row = Answer(
        user_id=uid,
        question="Did the reactor cool?",
        answer_md="The reactor cooled. [1]",
        sentences=[
            {
                "index": 1,
                "text": "The reactor cooled.",
                "char_start": 0,
                "char_end": 19,
                "verdict": "supported",
                "confidence": 0.9,
                "citations": [1],
                "flags": [],
                "method": "fake",
            }
        ],
        citations=[
            {
                "n": 1,
                "chunk_id": None,
                "document_id": str(doc.id),
                "version_id": version.id,
                "title": doc.title,
                "url": doc.url,
                "source": "Reactor feed",
                "date": "2026-03-04T00:00:00+00:00",
                "quote": SPAN,
                "char_start": start,
                "char_end": end,
                "page": None,
                "score": 0.5,
            }
        ],
        retrieval={},
        method="fake",
    )
    db.add(row)
    db.flush()
    return row


def _topic_with_claim(db, uid: uuid.UUID, doc: Document, version: DocumentVersion):
    start = SOURCE_TEXT.index(SPAN)
    claim = Claim(
        user_id=uid,
        document_id=doc.id,
        version_id=version.id,
        statement="The reactor cooled to 40 degrees.",
        quote=SPAN,
        char_start=start,
        char_end=start + len(SPAN),
        claim_hash=uuid.uuid4().hex,
        asserted_at=datetime(2026, 3, 4, tzinfo=UTC),
        observed_at=datetime.now(UTC),
        status="active",
    )
    db.add(claim)
    db.flush()
    cluster = ClaimCluster(user_id=uid, label="reactor temperature", size=1, conflict_count=0)
    db.add(cluster)
    db.flush()
    claim.cluster_id = cluster.id
    topic = Topic(user_id=uid, slug=f"reactor-{uuid.uuid4().hex[:6]}", label="Reactor")
    db.add(topic)
    db.flush()
    cluster.topic_id = topic.id
    page = WikiPage(
        user_id=uid,
        topic_id=topic.id,
        slug=topic.slug,
        title="Reactor",
        status="clean",
        body={
            "title": "Reactor",
            "summary": [{"text": "The reactor cooled.", "claim_ids": [claim.id]}],
            "sections": [],
            "see_also": [],
        },
        body_md="",
    )
    db.add(page)
    db.flush()
    return topic, claim


def _footnote_quotes(markdown: str) -> list[str]:
    return [line[len("    > ") :] for line in markdown.splitlines() if line.startswith("    > ")]


def test_every_footnote_quote_is_the_source_slice(db, user_factory) -> None:
    client, me = user_factory()
    uid = uuid.UUID(me["id"])
    doc, version = _document(db, uid)
    answer = _answer(db, uid, doc, version)
    topic, _claim = _topic_with_claim(db, uid, doc, version)

    answer_md = client.get(f"/v1/answers/{answer.id}/export?format=md").text
    topic_md = client.get(f"/v1/topics/{topic.id}/brief?format=md").text

    expected = version.clean_text[answer.citations[0]["char_start"] : answer.citations[0]["char_end"]]
    assert expected == SPAN
    assert _footnote_quotes(answer_md) == [expected]
    claim_start = SOURCE_TEXT.index(SPAN)
    assert _footnote_quotes(topic_md) == [version.clean_text[claim_start : claim_start + len(SPAN)]]
    assert "[supported] The reactor cooled." in answer_md
    assert "Did the reactor cool?" in answer_md


def test_export_headers_are_markdown_attachments(db, user_factory) -> None:
    client, me = user_factory()
    uid = uuid.UUID(me["id"])
    doc, version = _document(db, uid)
    answer = _answer(db, uid, doc, version)

    resp = client.get(f"/v1/answers/{answer.id}/export?format=md")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "text/markdown; charset=utf-8"
    assert resp.headers["content-disposition"] == f'attachment; filename="answer-{answer.id}.md"'


def test_other_tenant_gets_404_for_answer_and_topic(db, user_factory) -> None:
    owner, owner_me = user_factory()
    other, _other_me = user_factory()
    uid = uuid.UUID(owner_me["id"])
    doc, version = _document(db, uid)
    answer = _answer(db, uid, doc, version)
    topic, _claim = _topic_with_claim(db, uid, doc, version)

    assert owner.get(f"/v1/answers/{answer.id}/export?format=md").status_code == 200
    assert owner.get(f"/v1/topics/{topic.id}/brief?format=md").status_code == 200
    assert other.get(f"/v1/answers/{answer.id}/export?format=md").status_code == 404
    assert other.get(f"/v1/topics/{topic.id}/brief?format=md").status_code == 404


def test_mcp_export_brief_returns_markdown_with_question(db, user_factory) -> None:
    client, me = user_factory()
    uid = uuid.UUID(me["id"])
    doc, version = _document(db, uid)
    answer = _answer(db, uid, doc, version)
    user = CurrentUser(id=uid, email=me["email"], is_admin=False, scopes=frozenset({"read"}), via="pat")

    result = TOOLS["export_brief"](db, user, kind="answer", id=str(answer.id))

    assert result["format"] == "md"
    assert "Did the reactor cool?" in result["markdown"]
    assert result["markdown"].startswith("# Evidence brief: Did the reactor cool?")


def test_format_other_than_md_is_422(db, user_factory) -> None:
    client, me = user_factory()
    uid = uuid.UUID(me["id"])
    doc, version = _document(db, uid)
    answer = _answer(db, uid, doc, version)
    topic, _claim = _topic_with_claim(db, uid, doc, version)

    assert client.get(f"/v1/answers/{answer.id}/export?format=pdf").status_code == 422
    assert client.get(f"/v1/topics/{topic.id}/brief?format=html").status_code == 422
