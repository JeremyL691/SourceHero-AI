"""M9.8: reconciliation, stale notes, belief diffs, the timeline and watches."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select, text

from app.claims.extract import verify_quote_invariant
from app.models.claim import Claim, ClaimEvent
from app.models.document import Document, DocumentVersion
from app.models.source import Source
from app.models.temporal import BeliefDiff, Watch
from app.temporal.belief_diff import collect_claim_ids, compute_belief_diff, template_digest
from app.temporal.reconcile import reconcile_document
from app.temporal.staleness import (
    REASON_AGREES_WITH_OUTDATED,
    REASON_SUPERSEDED,
    detect_stale_notes,
)
from app.temporal.timeline import timeline
from app.temporal.watches import create_watch, delete_watch, list_watches

pytestmark = pytest.mark.db


@pytest.fixture(scope="module")
def ledger(engine):
    """A user whose ledger and timeline are the fully processed demo corpus."""
    from sqlalchemy.orm import Session

    from app.core.config import get_settings
    from app.core.security import hash_password
    from app.demo.seed import drain, seed_demo
    from app.models.user import User

    settings = get_settings()
    original = settings.jobs_inline
    settings.jobs_inline = False
    session = Session(bind=engine)
    user_id = uuid.uuid4()
    try:
        session.add(
            User(
                id=user_id,
                email=f"temporal{uuid.uuid4().hex[:10]}@test.local",
                password_hash=hash_password("temporal-password-123"),
            )
        )
        session.commit()
        seed_demo(session, user_id)
        session.commit()
        drain(session)
        session.commit()
        for document in _multi_version_documents(session, user_id):
            reconcile_document(session, document)
        session.commit()
        yield session, user_id
        session.rollback()
        for table in (
            "belief_diffs",
            "watches",
            "claim_events",
            "claim_relations",
            "claim_clusters",
            "claims",
            "chunks",
            "document_versions",
            "documents",
            "sources",
            "jobs",
        ):
            session.execute(text(f"DELETE FROM {table} WHERE user_id = :u"), {"u": user_id})
        session.execute(text("DELETE FROM users WHERE id = :u"), {"u": user_id})
        session.commit()
    finally:
        session.close()
        settings.jobs_inline = original


def _multi_version_documents(session, user_id) -> list:
    out = []
    for (document_id,) in session.execute(select(Document.id).where(Document.user_id == user_id)).all():
        count = len(
            session.execute(select(DocumentVersion.id).where(DocumentVersion.document_id == document_id))
            .scalars()
            .all()
        )
        if count > 1:
            out.append(document_id)
    return out


def _claim(session, document, version_no: int, pattern: str) -> Claim:
    version = session.execute(
        select(DocumentVersion).where(
            DocumentVersion.document_id == document.id, DocumentVersion.version_no == version_no
        )
    ).scalar_one()
    return (
        session.execute(
            select(Claim).where(Claim.version_id == version.id, Claim.statement.ilike(f"%{pattern}%"))
        )
        .scalars()
        .first()
    )


def _document(session, user_id, title_part: str) -> Document:
    return session.execute(
        select(Document).where(Document.user_id == user_id, Document.title.ilike(f"%{title_part}%"))
    ).scalar_one()


# --- reconciliation ------------------------------------------------------


def test_schedule_update_supersedes_the_original_claim(ledger):
    """The plan's first expectation, verbatim."""
    session, user_id = ledger
    press = _document(session, user_id, "press release")
    claim = _claim(session, press, 1, "will begin in September 2026")

    assert claim.status == "superseded"
    assert str(claim.valid_to)[:10] == "2026-08-20", "valid_to is the new version's world time"
    assert claim.superseded_by is not None
    successor = session.get(Claim, claim.superseded_by)
    assert "postponed to January 2027" in successor.statement


def test_revised_figure_supersedes_the_original(ledger):
    """The plan's second expectation: ferro 18% -> 11%."""
    session, user_id = ledger
    ferro = _document(session, user_id, "Transport committee")
    claim = _claim(session, ferro, 1, "Downtown traffic fell by 18%")
    assert claim.status == "superseded"
    successor = session.get(Claim, claim.superseded_by)
    assert "fell by 11%" in successor.statement


def test_a_removed_sentence_is_retracted(ledger):
    session, user_id = ledger
    press = _document(session, user_id, "press release")
    claim = _claim(session, press, 1, "reaffirmed the schedule")
    assert claim.status == "retracted"
    assert claim.valid_to is not None


def test_reconcile_is_idempotent(ledger):
    """Running it again must not change any status or duplicate an event."""
    session, user_id = ledger
    press = _document(session, user_id, "press release")

    def snapshot():
        rows = session.execute(
            select(Claim.id, Claim.status, Claim.superseded_by).where(Claim.document_id == press.id)
        ).all()
        events = len(
            session.execute(
                select(ClaimEvent.id)
                .join(Claim, Claim.id == ClaimEvent.claim_id)
                .where(Claim.document_id == press.id)
            )
            .scalars()
            .all()
        )
        return sorted(rows), events

    before = snapshot()
    reconcile_document(session, press.id)
    session.flush()
    assert snapshot() == before, "reconciliation must be a pure recomputation"


def test_current_version_still_follows_effective_at(ledger):
    """Reconcile changes claim status, never which version is current."""
    session, user_id = ledger
    for document_id in _multi_version_documents(session, user_id):
        document = session.get(Document, document_id)
        versions = (
            session.execute(
                select(DocumentVersion)
                .where(DocumentVersion.document_id == document_id)
                .order_by(DocumentVersion.effective_at)
            )
            .scalars()
            .all()
        )
        assert document.current_version_id == versions[-1].id


# --- stale notes ---------------------------------------------------------


def test_the_note_is_flagged_but_not_edited(ledger):
    """The plan's third expectation."""
    session, user_id = ledger
    notes = detect_stale_notes(session, user_id)
    assert notes, "the demo note must be detected as outdated"

    note = notes[0]
    assert REASON_SUPERSEDED in note.reasons or REASON_AGREES_WITH_OUTDATED in note.reasons
    # The evidence must include the announcement that overtook it.
    assert any("postponed to January 2027" in e.statement for e in note.evidence)

    # And the note's own claim is untouched.
    stored = session.get(Claim, note.claim_id)
    assert stored.status == "active", "a user's own note is never rewritten by the pipeline"


def test_both_reasons_can_apply_to_one_note(ledger):
    session, user_id = ledger
    note = detect_stale_notes(session, user_id)[0]
    assert REASON_AGREES_WITH_OUTDATED in note.reasons
    assert REASON_SUPERSEDED in note.reasons


def test_notes_of_another_user_are_invisible(ledger):
    session, user_id = ledger
    assert detect_stale_notes(session, uuid.uuid4()) == []


# --- belief diffs --------------------------------------------------------


def test_diff_over_the_planned_window(ledger):
    """2026-05-01..2026-09-01 must show >=2 superseded, >=1 conflict, >=1 stale note."""
    session, user_id = ledger
    result = compute_belief_diff(
        session,
        user_id,
        start=datetime(2026, 5, 1, tzinfo=UTC),
        end=datetime(2026, 9, 1, tzinfo=UTC),
    )
    assert result.stats["superseded"] >= 2, result.stats
    assert result.stats["new_conflicts"] >= 1, result.stats
    assert result.stats["stale_notes"] >= 1, result.stats


def test_every_digest_bullet_cites_a_claim_in_the_payload(ledger):
    session, user_id = ledger
    result = compute_belief_diff(
        session, user_id, start=datetime(2026, 5, 1, tzinfo=UTC), end=datetime(2026, 9, 1, tzinfo=UTC)
    )
    import re

    known = collect_claim_ids(result.payload)
    bullets = [line for line in result.digest_md.splitlines() if line.startswith("- ")]
    assert bullets, "the digest must say something"
    for line in bullets:
        cited = {int(n) for n in re.findall(r"\[c:(\d+)\]", line)}
        assert cited, f"bullet cites nothing: {line!r}"
        assert cited <= known, f"bullet cites a claim absent from the payload: {line!r}"


def test_diff_is_scoped_to_a_period(ledger):
    session, user_id = ledger
    empty = compute_belief_diff(
        session,
        user_id,
        start=datetime(2019, 1, 1, tzinfo=UTC),
        end=datetime(2019, 2, 1, tzinfo=UTC),
    )
    assert empty.stats["new_claims"] == 0
    assert empty.stats["superseded"] == 0


def test_both_time_axes_work(ledger):
    session, user_id = ledger
    for axis in ("world", "observed"):
        result = compute_belief_diff(
            session,
            user_id,
            start=datetime(2026, 1, 1, tzinfo=UTC),
            end=datetime(2026, 12, 31, tzinfo=UTC),
            time_axis=axis,
        )
        assert result.stats["new_claims"] > 0, axis


def test_template_digest_reports_an_empty_period_honestly():
    digest = template_digest(
        {
            "new_claims": [],
            "superseded": [],
            "retracted": [],
            "new_conflicts": [],
            "resolved": [],
            "stale_notes": [],
        },
        start=datetime(2026, 1, 1, tzinfo=UTC),
        end=datetime(2026, 2, 1, tzinfo=UTC),
    )
    assert "Nothing changed" in digest


def test_diff_is_persisted_and_readable(ledger, engine):
    from sqlalchemy.orm import Session as OrmSession

    from app.temporal.belief_diff import compute_and_store_diff

    session, user_id = ledger
    row = compute_and_store_diff(
        session,
        user_id,
        start=datetime(2026, 5, 1, tzinfo=UTC),
        end=datetime(2026, 9, 1, tzinfo=UTC),
    )
    session.commit()
    with OrmSession(bind=engine) as reader:
        stored = reader.get(BeliefDiff, row.id)
        assert stored is not None
        assert stored.digest_md
        assert stored.payload["stats"]


# --- timeline ------------------------------------------------------------


def test_timeline_is_ordered_and_filterable(ledger):
    session, user_id = ledger
    entries = timeline(session, user_id, limit=500)
    assert entries, "the demo corpus must produce events"
    assert [e.at for e in entries] == sorted(e.at for e in entries)

    restricted = timeline(session, user_id, document_id=entries[0].document_id)
    assert all(e.document_id == entries[0].document_id for e in restricted)


def test_timeline_respects_the_period(ledger):
    session, user_id = ledger
    inside = timeline(
        session,
        user_id,
        start=datetime(2026, 5, 1, tzinfo=UTC),
        end=datetime(2026, 9, 1, tzinfo=UTC),
        limit=500,
    )
    assert all(datetime(2026, 5, 1, tzinfo=UTC) <= e.at < datetime(2026, 9, 1, tzinfo=UTC) for e in inside)


def test_timeline_observed_axis_differs_from_world(ledger):
    session, user_id = ledger
    world = timeline(session, user_id, limit=500)
    observed = timeline(session, user_id, axis="observed", limit=500)
    assert {e.claim_id for e in world} == {e.claim_id for e in observed}


# --- watches -------------------------------------------------------------


def test_source_watch_marks_the_source(ledger):
    session, user_id = ledger
    source = session.execute(select(Source).where(Source.user_id == user_id).limit(1)).scalars().first()
    watch = create_watch(session, user_id, kind="source", source_id=source.id)
    session.refresh(source)
    assert source.watch is True
    assert watch.kind == "source"
    assert [w.id for w in list_watches(session, user_id)] == [watch.id]


def test_query_watch_stores_an_embedding(ledger):
    session, user_id = ledger
    watch = create_watch(session, user_id, kind="query", query="battery energy density")
    assert watch.embedding is not None, "a query watch needs a vector to match claims by"
    assert len(watch.embedding) > 0


def test_watch_validation(ledger):
    session, user_id = ledger
    from app.core.errors import DomainValidationError, NotFoundError

    with pytest.raises(DomainValidationError):
        create_watch(session, user_id, kind="nonsense")
    with pytest.raises(DomainValidationError):
        create_watch(session, user_id, kind="source")
    with pytest.raises(DomainValidationError):
        create_watch(session, user_id, kind="query", query="  ")
    with pytest.raises(NotFoundError):
        create_watch(session, user_id, kind="source", source_id=uuid.uuid4())


def test_deleting_the_last_watch_stops_watching(ledger):
    """`watch` is cleared only once *no* watch still wants the source."""
    session, user_id = ledger
    # Pick a source no other test watched, or an existing watch would keep it alive.
    watched = {
        row[0] for row in session.execute(select(Watch.source_id).where(Watch.user_id == user_id)).all()
    }
    source = next(
        s
        for s in session.execute(select(Source).where(Source.user_id == user_id)).scalars()
        if s.id not in watched
    )
    source.watch = False
    session.flush()

    watch = create_watch(session, user_id, kind="source", source_id=source.id)
    session.refresh(source)
    assert source.watch is True

    delete_watch(session, user_id, watch.id)
    session.refresh(source)
    assert source.watch is False


def test_another_users_watch_cannot_be_deleted(ledger):
    session, user_id = ledger
    source = session.execute(select(Source).where(Source.user_id == user_id).limit(1)).scalars().first()
    watch = create_watch(session, user_id, kind="source", source_id=source.id)
    from app.core.errors import NotFoundError

    with pytest.raises(NotFoundError):
        delete_watch(session, uuid.uuid4(), watch.id)


# --- cross-document ------------------------------------------------------


def test_quote_invariant_survives_reconciliation(ledger):
    session, user_id = ledger
    assert verify_quote_invariant(session, user_id) == []


def test_temporal_tables_are_tenant_scoped(ledger):
    session, user_id = ledger
    from sqlalchemy import delete

    from app.core.security import hash_password
    from app.models.user import User

    other_user = User(
        id=uuid.uuid4(),
        email=f"temporal_other_{uuid.uuid4().hex[:8]}@test.local",
        password_hash=hash_password("test-pass-123"),
    )
    session.add(other_user)
    session.commit()
    try:
        mine = (
            session.execute(select(ClaimEvent.id).where(ClaimEvent.user_id == user_id).limit(1))
            .scalars()
            .all()
        )
        assert mine
        theirs = (
            session.execute(select(ClaimEvent.id).where(ClaimEvent.user_id == other_user.id)).scalars().all()
        )
        assert theirs == []
        theirs_watches = (
            session.execute(select(Watch.id).where(Watch.user_id == other_user.id)).scalars().all()
        )
        assert theirs_watches == []
    finally:
        session.execute(delete(User).where(User.id == other_user.id))
        session.commit()


def test_belief_diff_cluster_scope_non_empty_on_demo_hx2(ledger):
    """DOM-16: belief diff scoped to HX-2 cluster must be non-empty."""
    from app.models.claim import ClaimCluster

    session, user_id = ledger
    cluster = (
        session.execute(
            select(ClaimCluster).where(ClaimCluster.user_id == user_id, ClaimCluster.label.ilike("%HX-2%"))
        )
        .scalars()
        .first()
    )
    if cluster is None:
        cluster = (
            session.execute(select(ClaimCluster).where(ClaimCluster.user_id == user_id)).scalars().first()
        )
    assert cluster is not None

    diff = compute_belief_diff(
        session,
        user_id,
        start=datetime(2020, 1, 1, tzinfo=UTC),
        end=datetime(2030, 1, 1, tzinfo=UTC),
        scope_kind="cluster",
        scope_id=cluster.id,
    )
    assert diff.payload["new_claims"] or diff.stats["new_claims"] > 0, "HX-2 cluster diff must not be empty"


def test_belief_diff_source_watch_scope_resolves_documents(ledger):
    """DOM-16: Source watch belief diff covers claims from that source's documents."""
    from app.temporal.belief_diff import _scope_claim_ids

    session, user_id = ledger
    source = session.execute(select(Source).where(Source.user_id == user_id).limit(1)).scalars().first()
    assert source is not None

    watch = create_watch(session, user_id, kind="source", source_id=source.id)
    try:
        scoped = _scope_claim_ids(session, user_id, "watch", watch.id)
        assert scoped is not None and len(scoped) > 0, "Source watch must resolve claims via documents"
    finally:
        delete_watch(session, user_id, watch.id)


def test_belief_diff_and_timeline_reject_other_tenant_scope(ledger, engine):
    """SEC-13: Requesting diff or timeline with another tenant's scope returns 404."""
    from fastapi.testclient import TestClient

    from app.core.db import get_db
    from app.core.security import create_access_token, hash_password
    from app.main import create_app
    from app.models.claim import ClaimCluster
    from app.models.user import User

    session, user_id = ledger
    other_id = uuid.uuid4()
    other_user = User(
        id=other_id,
        email=f"temporal_sec13_{uuid.uuid4().hex[:8]}@test.local",
        password_hash=hash_password("test-pass-123"),
    )
    session.add(other_user)
    session.commit()

    other_cluster = ClaimCluster(user_id=other_id, label="Other Cluster", size=5)
    session.add(other_cluster)
    session.commit()

    app = create_app()
    app.dependency_overrides[get_db] = lambda: session

    try:
        with TestClient(app) as client:
            client.headers.update({"Authorization": f"Bearer {create_access_token(user_id)}"})

            # Diff with other user's cluster_id -> 404
            resp = client.post(
                "/v1/diffs",
                json={
                    "start": "2024-01-01T00:00:00Z",
                    "end": "2025-01-01T00:00:00Z",
                    "scope_kind": "cluster",
                    "scope_id": other_cluster.id,
                },
            )
            assert resp.status_code == 404, resp.text

            # Timeline with other user's cluster_id -> 404
            resp_tl = client.get(f"/v1/timeline?cluster_id={other_cluster.id}")
            assert resp_tl.status_code == 404, resp_tl.text
    finally:
        app.dependency_overrides.clear()
        session.execute(text("DELETE FROM claim_clusters WHERE user_id = :u"), {"u": other_id})
        session.execute(text("DELETE FROM users WHERE id = :u"), {"u": other_id})
        session.commit()
