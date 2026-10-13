"""Tenant isolation matrix.

Every list / detail / mutate endpoint that exposes user data must appear here. When
you add a resource endpoint, add its case -- that is the point of this file.

For each case user A creates the resource and user B checks three things:
  * it is absent from B's list,
  * B cannot reach it by id -- 404, not 403, since a 403 would confirm the id exists,
  * B cannot mutate it.
"""

from __future__ import annotations

import io
import uuid
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.core import time as clock
from app.jobs import queue as q
from app.models.answer import Answer
from app.models.chunk import IngestionRun
from app.models.claim import Claim, ClaimCluster, ClaimEvent, ClaimRelation
from app.models.document import Document, DocumentVersion
from app.models.source import Source
from app.models.temporal import BeliefDiff
from app.models.wiki import LintFinding, Topic, WikiPage

pytestmark = pytest.mark.db


@dataclass
class ResourceCase:
    name: str
    create: Callable[[Any, Session], dict]
    list_path: str | None = None
    item_path: str | None = None
    detail_method: str = "GET"
    detail_paths: tuple[tuple[str, str], ...] = ()
    mutations: tuple[tuple[str, str, dict | None], ...] = ()
    patch: tuple[str, dict] | None = None
    id_key: str = "id"
    list_key: str = "id"
    skip_list_check: bool = False


RESOURCE_CASES: list[ResourceCase] = []


def register(case: ResourceCase) -> ResourceCase:
    RESOURCE_CASES.append(case)
    return case


def _user_id(client) -> uuid.UUID:
    resp = client.get("/v1/auth/me")
    assert resp.status_code == 200
    return uuid.UUID(resp.json()["id"])


# --- registered cases ----------------------------------------------------

# Tokens
register(
    ResourceCase(
        name="api_tokens",
        create=lambda c, db: c.post("/v1/tokens", json={"name": "secret"}).json(),
        list_path="/v1/tokens",
        item_path="/v1/tokens/{id}",
        detail_method="DELETE",
    )
)


# Sources
def _create_source(c, db):
    return c.post(
        "/v1/sources",
        json={
            "name": f"src_{uuid.uuid4().hex[:6]}",
            "source_type": "webpage",
            "url": "https://example.com/item",
        },
    ).json()


register(
    ResourceCase(
        name="sources",
        create=_create_source,
        list_path="/v1/sources",
        item_path="/v1/sources/{id}",
        detail_paths=(("GET", "/v1/sources/{id}"),),
        mutations=(
            ("PATCH", "/v1/sources/{id}", {"name": "renamed"}),
            ("POST", "/v1/sources/{id}/refresh", {}),
            (
                "POST",
                "/v1/sources/{id}/wayback",
                {"archive_url": "https://web.archive.org/web/2026/https://example.com"},
            ),
            ("DELETE", "/v1/sources/{id}", None),
        ),
    )
)


# Documents
def _create_document(c, db):
    uid = _user_id(c)
    source = Source(user_id=uid, name="Source For Doc", source_type="webpage", url="https://example.com")
    db.add(source)
    db.flush()
    doc = Document(
        user_id=uid, source_id=source.id, title="Doc Title", identity_key=f"url:{uuid.uuid4().hex}"
    )
    db.add(doc)
    db.flush()
    ver = DocumentVersion(
        user_id=uid,
        document_id=doc.id,
        version_no=1,
        raw_text="Hello world document text",
        clean_text="Hello world document text",
        content_hash="h1",
        effective_at=datetime.now(UTC),
    )
    db.add(ver)
    db.commit()
    return {"id": str(doc.id), "version_id": ver.id}


register(
    ResourceCase(
        name="documents",
        create=_create_document,
        list_path="/v1/documents",
        item_path="/v1/documents/{id}",
        detail_paths=(
            ("GET", "/v1/documents/{id}"),
            ("GET", "/v1/documents/{id}/versions"),
        ),
    )
)


# Document versions
def _create_document_version(c, db):
    doc_res = _create_document(c, db)
    return {"id": doc_res["version_id"], "doc_id": doc_res["id"]}


register(
    ResourceCase(
        name="document_versions",
        create=_create_document_version,
        item_path="/v1/documents/{doc_id}/versions/{id}",
        skip_list_check=True,
    )
)


# Ingestion runs
def _create_ingestion_run(c, db):
    uid = _user_id(c)
    source = Source(user_id=uid, name="Src", source_type="webpage", url="https://example.com")
    db.add(source)
    db.flush()
    run = IngestionRun(user_id=uid, source_id=source.id, status="succeeded")
    db.add(run)
    db.commit()
    return {"id": str(run.id)}


register(
    ResourceCase(
        name="ingestion_runs",
        create=_create_ingestion_run,
        list_path="/v1/ingestion-runs",
        item_path="/v1/ingestion-runs/{id}",
    )
)


# Jobs
def _create_job(c, db):
    uid = _user_id(c)
    job = q.enqueue(
        db, "index_version", {}, user_id=uid, dedupe_key=f"job_{uuid.uuid4().hex}", now=clock.utcnow()
    )
    db.commit()
    return {"id": job.id}


register(
    ResourceCase(
        name="jobs",
        create=_create_job,
        list_path="/v1/jobs",
        item_path="/v1/jobs/{id}",
        mutations=(
            ("POST", "/v1/jobs/{id}/retry", None),
            ("POST", "/v1/jobs/{id}/cancel", None),
        ),
    )
)


# Answers
def _create_answer(c, db):
    uid = _user_id(c)
    ans = Answer(
        user_id=uid,
        question="What is this?",
        answer_md="Answer md",
        sentences=[
            {"index": 0, "text": "Answer text", "char_start": 0, "char_end": 11, "verdict": "supported"}
        ],
        citations=[],
        retrieval={},
        method="heuristic",
    )
    db.add(ans)
    db.commit()
    return {"id": str(ans.id)}


register(
    ResourceCase(
        name="answers",
        create=_create_answer,
        list_path="/v1/answers",
        item_path="/v1/answers/{id}",
        mutations=(("POST", "/v1/answers/{id}/save-note", {"title": "Note title"}),),
    )
)


# Claims
def _create_claim(c, db):
    uid = _user_id(c)
    src = Source(user_id=uid, name="Source", source_type="webpage", url="https://example.com")
    db.add(src)
    db.flush()
    doc = Document(user_id=uid, source_id=src.id, title="Doc", identity_key=f"url:{uuid.uuid4().hex}")
    db.add(doc)
    db.flush()
    ver = DocumentVersion(
        user_id=uid,
        document_id=doc.id,
        version_no=1,
        raw_text="Claim text",
        clean_text="Claim text",
        content_hash="ch1",
        effective_at=datetime.now(UTC),
    )
    db.add(ver)
    db.flush()
    cluster = ClaimCluster(user_id=uid, label="test", size=1, conflict_count=0)
    db.add(cluster)
    db.flush()
    cl = Claim(
        user_id=uid,
        document_id=doc.id,
        version_id=ver.id,
        cluster_id=cluster.id,
        claim_hash=f"ch_{uuid.uuid4().hex}",
        statement="Claim statement",
        quote="Claim text",
        status="active",
        asserted_at=datetime.now(UTC),
        observed_at=datetime.now(UTC),
        char_start=0,
        char_end=10,
    )
    db.add(cl)
    db.commit()
    return {"id": cl.id, "cluster_id": cluster.id}


register(
    ResourceCase(
        name="claims",
        create=_create_claim,
        list_path="/v1/claims",
        item_path="/v1/claims/{id}",
    )
)


# Clusters
def _create_cluster(c, db):
    res = _create_claim(c, db)
    return {"id": res["cluster_id"]}


register(
    ResourceCase(
        name="clusters",
        create=_create_cluster,
        list_path="/v1/clusters",
        item_path="/v1/clusters/{id}",
    )
)


# Conflicts & Relations
def _create_relation(c, db):
    uid = _user_id(c)
    res = _create_claim(c, db)
    cl1 = db.get(Claim, res["id"])
    cl2 = Claim(
        user_id=uid,
        document_id=cl1.document_id,
        version_id=cl1.version_id,
        cluster_id=cl1.cluster_id,
        claim_hash=f"ch_{uuid.uuid4().hex}",
        statement="Contradicting statement",
        quote="Claim text",
        status="active",
        asserted_at=datetime.now(UTC),
        observed_at=datetime.now(UTC),
        char_start=0,
        char_end=10,
    )
    db.add(cl2)
    db.flush()
    rel = ClaimRelation(
        user_id=uid,
        a_claim_id=cl1.id,
        b_claim_id=cl2.id,
        relation="contradicts",
        confidence=0.9,
    )
    db.add(rel)
    db.commit()
    return {"id": rel.id}


register(
    ResourceCase(
        name="conflicts",
        create=_create_relation,
        list_path="/v1/conflicts",
        item_path="/v1/relations/{id}",
        mutations=(("PATCH", "/v1/relations/{id}", {"user_label": "confirmed"}),),
    )
)


# Diffs
def _create_diff(c, db):
    uid = _user_id(c)
    diff = BeliefDiff(
        id=uuid.uuid4(),
        user_id=uid,
        scope_kind="all",
        period_start=datetime(2026, 1, 1, tzinfo=UTC),
        period_end=datetime(2026, 1, 10, tzinfo=UTC),
        payload={"stats": {"new_claims": 1}},
        digest_md="diff summary",
    )
    db.add(diff)
    db.commit()
    return {"id": str(diff.id)}


register(
    ResourceCase(
        name="diffs",
        create=_create_diff,
        list_path="/v1/diffs",
        item_path="/v1/diffs/{id}",
    )
)

# Watches
register(
    ResourceCase(
        name="watches",
        create=lambda c, db: c.post("/v1/watches", json={"kind": "query", "query": "battery"}).json(),
        list_path="/v1/watches",
        item_path="/v1/watches/{id}",
        mutations=(("DELETE", "/v1/watches/{id}", None),),
    )
)


# Timeline
def _create_timeline_event(c, db):
    res = _create_claim(c, db)
    cl = db.get(Claim, res["id"])
    ev = ClaimEvent(
        user_id=cl.user_id,
        claim_id=cl.id,
        event="asserted",
        at=datetime.now(UTC),
        observed_at=datetime.now(UTC),
    )
    db.add(ev)
    db.commit()
    return {"id": ev.id}


register(
    ResourceCase(
        name="timeline",
        create=_create_timeline_event,
        list_path="/v1/timeline",
        skip_list_check=True,
    )
)

# Stale notes
register(
    ResourceCase(
        name="stale_notes",
        create=_create_claim,
        list_path="/v1/stale-notes",
        skip_list_check=True,
    )
)


# Topics & Wiki
def _create_wiki_page(c, db):
    uid = _user_id(c)
    slug = f"topic-{uuid.uuid4().hex[:6]}"
    topic = Topic(user_id=uid, slug=slug, label="Topic Label")
    db.add(topic)
    db.flush()
    page = WikiPage(
        user_id=uid,
        topic_id=topic.id,
        slug=slug,
        title="Topic Title",
        body_md="# Topic Title\nContent",
        input_hash="hash",
        revision=1,
    )
    db.add(page)
    finding = LintFinding(
        user_id=uid,
        page_id=page.id,
        kind="orphan_page",
        severity="info",
        detail={"target": slug},
    )
    db.add(finding)
    db.commit()
    return {"id": finding.id, "slug": slug, "topic_id": topic.id, "finding_id": finding.id, "revision": 1}


register(
    ResourceCase(
        name="topics",
        create=_create_wiki_page,
        list_path="/v1/topics",
        list_key="slug",
        id_key="slug",
    )
)

register(
    ResourceCase(
        name="wiki_pages",
        create=_create_wiki_page,
        list_path="/v1/wiki/pages",
        item_path="/v1/wiki/pages/{id}",
        list_key="slug",
        id_key="slug",
        detail_paths=(
            ("GET", "/v1/wiki/pages/{id}"),
            ("GET", "/v1/wiki/pages/{id}/revisions"),
            ("GET", "/v1/wiki/pages/{id}/revisions/{revision}/diff"),
        ),
        mutations=(("POST", "/v1/wiki/pages/{id}/compile", None),),
    )
)

register(
    ResourceCase(
        name="wiki_lint",
        create=_create_wiki_page,
        list_path="/v1/wiki/lint/findings",
        list_key="id",
        id_key="finding_id",
    )
)


# --- runner --------------------------------------------------------------


def _register_and_auth(client) -> dict[str, str]:
    resp = client.post(
        "/v1/auth/register",
        json={"email": f"t{uuid.uuid4().hex[:12]}@test.local", "password": "isolation-pass-123"},
    )
    assert resp.status_code == 201, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.mark.parametrize("case", RESOURCE_CASES, ids=lambda c: c.name)
def test_resource_is_invisible_to_another_user(make_client, db, case: ResourceCase):
    a = make_client()
    a.headers.update(_register_and_auth(a))
    created = case.create(a, db)
    resource_id = str(created.get(case.id_key, ""))

    b = make_client()
    b.headers.update(_register_and_auth(b))

    # 1) List check
    if case.list_path is not None:
        mine = a.get(case.list_path)
        assert mine.status_code == 200, mine.text
        if not case.skip_list_check and resource_id:
            items = mine.json() if isinstance(mine.json(), list) else mine.json().get("items", [])
            assert any(str(r.get(case.list_key)) == resource_id for r in items), (
                f"{case.name}: the owner cannot see their own resource in {case.list_path}"
            )

        theirs = b.get(case.list_path)
        assert theirs.status_code == 200, theirs.text
        theirs_items = theirs.json() if isinstance(theirs.json(), list) else theirs.json().get("items", [])
        if resource_id:
            assert all(str(r.get(case.list_key)) != resource_id for r in theirs_items), (
                f"{case.name} leaks across tenants in {case.list_path}"
            )

    def _fmt(tmpl: str) -> str:
        fmt_kwargs = dict(created)
        fmt_kwargs["id"] = resource_id
        if "doc_id" not in fmt_kwargs and "id" in created:
            fmt_kwargs["doc_id"] = created["id"]
        return tmpl.format(**fmt_kwargs)

    # 2) Direct item endpoint
    if case.item_path is not None and resource_id:
        resp = b.request(case.detail_method, _fmt(case.item_path))
        assert resp.status_code in (404, 405), (
            f"{case.name} {case.detail_method} {case.item_path}: got {resp.status_code}, want 404/405"
        )
        if resp.status_code == 404:
            assert "leak" not in resp.text.lower()

    # 3) Additional detail paths
    for method, path_tmpl in case.detail_paths:
        target = _fmt(path_tmpl)
        resp = b.request(method, target)
        assert resp.status_code in (404, 405), (
            f"{case.name} {method} {target}: got {resp.status_code}, want 404/405"
        )

    # 4) Mutations
    for method, path_tmpl, payload in case.mutations:
        target = _fmt(path_tmpl)
        resp = b.request(method, target, json=payload if payload is not None else {})
        assert resp.status_code in (404, 405), (
            f"{case.name} mutation {method} {target}: got {resp.status_code}, want 404/405"
        )

    # 5) Legacy patch field
    if case.patch is not None and resource_id:
        path, payload = case.patch
        patched = b.patch(_fmt(path), json=payload)
        assert patched.status_code in (404, 405)


def test_matrix_is_not_empty():
    """Guards against the matrix silently rotting into a no-op."""
    assert RESOURCE_CASES, "no resource cases registered"
    # Fix the tautological duplicate check: assert unique names across registered cases
    assert len({c.name for c in RESOURCE_CASES}) == len(RESOURCE_CASES), "duplicate case names"


@pytest.mark.parametrize(
    "path",
    [
        "/v1/tokens",
        "/v1/auth/me",
        "/v1/sources",
        "/v1/documents",
        "/v1/jobs",
        "/v1/claims",
        "/v1/clusters",
        "/v1/conflicts",
        "/v1/diffs",
        "/v1/watches",
        "/v1/topics",
        "/v1/wiki/pages",
    ],
)
def test_unauthenticated_requests_are_rejected(make_client, path):
    anon = make_client()
    resp = anon.get(path)
    # Auth rejection is 401 (with WWW-Authenticate: Bearer)
    assert resp.status_code == 401, f"{path} answered {resp.status_code} without credentials"
    assert resp.headers.get("www-authenticate") == "Bearer"


# --- Search, Verify, Ask Isolation ---------------------------------------


def test_search_and_ask_isolation(make_client, db):
    a = make_client()
    a.headers.update(_register_and_auth(a))
    uid_a = _user_id(a)

    src = Source(user_id=uid_a, name="Secret Source", source_type="webpage", url="https://example.com/secret")
    db.add(src)
    db.flush()
    doc = Document(
        user_id=uid_a, source_id=src.id, title="TopSecret Research", identity_key=f"url:{uuid.uuid4().hex}"
    )
    db.add(doc)
    db.flush()
    ver = DocumentVersion(
        user_id=uid_a,
        document_id=doc.id,
        version_no=1,
        raw_text="Quantum battery secret formulation 998877",
        clean_text="Quantum battery secret formulation 998877",
        content_hash="hsec",
        effective_at=datetime.now(UTC),
    )
    db.add(ver)
    db.commit()

    # Index chunks
    from app.indexing.indexer import index_version

    index_version(db, ver.id)

    b = make_client()
    b.headers.update(_register_and_auth(b))

    search_resp = b.post("/v1/search", json={"query": "Quantum battery secret formulation 998877", "k": 5})
    assert search_resp.status_code == 200
    assert len(search_resp.json()["hits"]) == 0

    ask_resp = b.post(
        "/v1/ask", params={"stream": "false"}, json={"question": "What is secret formulation 998877?"}
    )
    assert ask_resp.status_code == 200
    # User B must not cite or see A's secret document
    assert "998877" not in ask_resp.json().get("answer_md", "")


# --- Wiki Export Isolation -----------------------------------------------


def test_wiki_export_isolation(make_client, db):
    a = make_client()
    a.headers.update(_register_and_auth(a))
    _create_wiki_page(a, db)

    b = make_client()
    b.headers.update(_register_and_auth(b))
    resp = b.get("/v1/wiki/export")
    assert resp.status_code == 200
    archive = zipfile.ZipFile(io.BytesIO(resp.content))
    # B's zip archive must only contain empty _index.md and none of A's topics/pages
    for name in archive.namelist():
        if name != "wiki/_index.md":
            pytest.fail(f"User B's wiki export contains unexpected file: {name}")


# --- Mixed-ID Probes -----------------------------------------------------


def test_mixed_id_document_diff_from_to(make_client, db):
    """User B requests diff with their own 'from' and User A's 'to'."""
    a = make_client()
    a.headers.update(_register_and_auth(a))
    uid_a = _user_id(a)

    src_a = Source(user_id=uid_a, name="Src A", source_type="webpage", url="https://example.com/a")
    db.add(src_a)
    db.flush()
    doc_a = Document(user_id=uid_a, source_id=src_a.id, title="Doc A", identity_key=f"url:{uuid.uuid4().hex}")
    db.add(doc_a)
    db.flush()
    ver_a1 = DocumentVersion(
        user_id=uid_a,
        document_id=doc_a.id,
        version_no=1,
        raw_text="A1",
        clean_text="A1",
        content_hash="ha1",
        effective_at=datetime.now(UTC),
    )
    ver_a2 = DocumentVersion(
        user_id=uid_a,
        document_id=doc_a.id,
        version_no=2,
        raw_text="A2 Secret",
        clean_text="A2 Secret",
        content_hash="ha2",
        effective_at=datetime.now(UTC),
    )
    db.add_all([ver_a1, ver_a2])
    db.commit()

    b = make_client()
    b.headers.update(_register_and_auth(b))
    uid_b = _user_id(b)

    src_b = Source(user_id=uid_b, name="Src B", source_type="webpage", url="https://example.com/b")
    db.add(src_b)
    db.flush()
    doc_b = Document(user_id=uid_b, source_id=src_b.id, title="Doc B", identity_key=f"url:{uuid.uuid4().hex}")
    db.add(doc_b)
    db.flush()
    ver_b1 = DocumentVersion(
        user_id=uid_b,
        document_id=doc_b.id,
        version_no=1,
        raw_text="B1",
        clean_text="B1",
        content_hash="hb1",
        effective_at=datetime.now(UTC),
    )
    db.add(ver_b1)
    db.commit()

    # B attempts to read A's version by passing to=ver_a2.id on doc_b
    resp = b.get(f"/v1/documents/{doc_b.id}/diff?from={ver_b1.id}&to={ver_a2.id}")
    assert resp.status_code == 404


def test_mixed_id_document_claims(make_client, db):
    """User B attempts to fetch claims of User A's document."""
    a = make_client()
    a.headers.update(_register_and_auth(a))
    res_a = _create_claim(a, db)
    cl_a = db.get(Claim, res_a["id"])

    b = make_client()
    b.headers.update(_register_and_auth(b))
    resp = b.get(f"/v1/documents/{cl_a.document_id}/claims")
    assert resp.status_code == 404


def test_mixed_id_diffs_scope_cluster(make_client, db):
    """User A attempts to compute belief diff for User B's cluster."""
    b = make_client()
    b.headers.update(_register_and_auth(b))
    res_b = _create_claim(b, db)
    b_cluster_id = res_b["cluster_id"]

    a = make_client()
    a.headers.update(_register_and_auth(a))

    resp = a.post(
        "/v1/diffs",
        json={
            "start": "2026-01-01T00:00:00Z",
            "end": "2026-02-01T00:00:00Z",
            "scope_kind": "cluster",
            "scope_id": b_cluster_id,
        },
    )
    assert resp.status_code == 404


def test_mixed_id_patch_relation(make_client, db):
    """User B attempts to patch User A's relation."""
    a = make_client()
    a.headers.update(_register_and_auth(a))
    res_a = _create_relation(a, db)

    b = make_client()
    b.headers.update(_register_and_auth(b))
    resp = b.patch(f"/v1/relations/{res_a['id']}", json={"user_label": "confirmed"})
    assert resp.status_code == 404


def test_mixed_id_retry_job(make_client, db):
    """User B attempts to retry User A's job."""
    a = make_client()
    a.headers.update(_register_and_auth(a))
    job_a = _create_job(a, db)

    b = make_client()
    b.headers.update(_register_and_auth(b))
    resp = b.post(f"/v1/jobs/{job_a['id']}/retry")
    assert resp.status_code == 404


def test_mixed_id_save_note(make_client, db):
    """User B attempts to save User A's answer as note."""
    a = make_client()
    a.headers.update(_register_and_auth(a))
    ans_a = _create_answer(a, db)

    b = make_client()
    b.headers.update(_register_and_auth(b))
    resp = b.post(f"/v1/answers/{ans_a['id']}/save-note", json={"title": "Stolen note"})
    assert resp.status_code == 404


# Library: collections and tags
def _create_collection(c, db):
    return c.post("/v1/collections", json={"name": f"col_{uuid.uuid4().hex[:8]}"}).json()


register(
    ResourceCase(
        name="collections",
        create=_create_collection,
        list_path="/v1/collections",
        item_path="/v1/collections/{id}",
        detail_method="DELETE",
        patch=("/v1/collections/{id}", {"description": "changed"}),
    )
)


def _create_tag(c, db):
    return c.post("/v1/tags", json={"name": f"tag_{uuid.uuid4().hex[:8]}"}).json()


register(
    ResourceCase(
        name="tags",
        create=_create_tag,
        list_path="/v1/tags",
        item_path="/v1/tags/{id}",
        detail_method="DELETE",
    )
)


# Schedules
def _create_schedule(c, db):
    return c.post(
        "/v1/schedules",
        json={"job_type": "plan_topics", "name": f"sched_{uuid.uuid4().hex[:6]}", "hour": 6},
    ).json()


register(
    ResourceCase(
        name="schedules",
        create=_create_schedule,
        list_path="/v1/schedules",
        item_path="/v1/schedules/{id}",
        detail_method="DELETE",
        detail_paths=(("GET", "/v1/schedules/{id}/runs"),),
        patch=("/v1/schedules/{id}", {"enabled": False}),
    )
)
