"""Evidence briefs: an answer or a topic, exported as Markdown with source footnotes.

This layer loads the data and enforces tenant ownership. The text itself is produced by
`app.briefs.render`, which has no database access. Every footnote quote is sliced from the
stored `DocumentVersion.clean_text` at the recorded offsets, never copied from a stored
`quote` field, so a reader can check it against the source.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.briefs.render import Line, Section, Source, render_brief
from app.core import time as clock
from app.core.errors import NotFoundError
from app.models.answer import Answer
from app.models.claim import Claim, ClaimCluster
from app.models.document import Document, DocumentVersion
from app.models.wiki import Topic, WikiPage

BELIEF_WINDOW_DAYS = 30


def _source(
    db: Session, user_id: uuid.UUID, version_id: int, start: int, end: int, title, url, date
) -> Source:
    version = db.get(DocumentVersion, version_id)
    if version is None or version.user_id != user_id:
        quote = "(source text unavailable)"
    else:
        quote = version.clean_text[start:end]
    return Source(title=title or "", url=url, date=date, quote=quote)


def _citation_source(db: Session, user_id: uuid.UUID, cit: dict) -> Source:
    return _source(
        db,
        user_id,
        cit["version_id"],
        cit["char_start"],
        cit["char_end"],
        cit.get("title"),
        cit.get("url"),
        (cit.get("date") or "")[:10] or None,
    )


def _claim_source(db: Session, user_id: uuid.UUID, claim_id: int) -> Source | None:
    claim = db.get(Claim, claim_id)
    if claim is None or claim.user_id != user_id:
        return None
    doc = db.get(Document, claim.document_id)
    date = doc.published_at.date().isoformat() if doc and doc.published_at else None
    return _source(
        db,
        user_id,
        claim.version_id,
        claim.char_start,
        claim.char_end,
        doc.title if doc else "",
        doc.url if doc else None,
        date,
    )


def build_answer_brief(db: Session, user_id: uuid.UUID, answer_id: uuid.UUID) -> str:
    from app.claims.camps import conflicts_for_chunks

    row = db.get(Answer, answer_id)
    if row is None or row.user_id != user_id:
        raise NotFoundError("answer not found")

    citations = {int(c["n"]): c for c in row.citations or []}
    sentences: list[Line] = []
    for s in row.sentences or []:
        sources = tuple(
            _citation_source(db, user_id, citations[int(n)])
            for n in s.get("citations", [])
            if int(n) in citations
        )
        sentences.append(Line(text=s["text"], verdict=s.get("verdict"), sources=sources))

    disputes: list[Line] = []
    chunk_ids = [c["chunk_id"] for c in citations.values() if c.get("chunk_id") is not None]
    for dispute in conflicts_for_chunks(db, user_id, chunk_ids):
        sources = tuple(
            src
            for src in (
                _claim_source(db, user_id, dispute["claim_a_id"]),
                _claim_source(db, user_id, dispute["claim_b_id"]),
            )
            if src is not None
        )
        disputes.append(Line(text=f"{dispute['a']} ⟂ {dispute['b']}", sources=sources))

    return render_brief(
        title=f"Evidence brief: {row.question}",
        subtitle=f"Answer from {row.created_at:%Y-%m-%d} · method: {row.method}",
        sections=[
            Section("Answer", tuple(sentences), empty="The answer has no sentences."),
            Section("Related disputes", tuple(disputes), empty="No disputes among the cited sources."),
        ],
    )


def build_topic_brief(db: Session, user_id: uuid.UUID, topic_id: int) -> str:
    from app.claims.camps import cluster_camp_inputs, compute_camps
    from app.llm.schemas import WikiBody
    from app.temporal.belief_diff import compute_belief_diff

    topic = db.get(Topic, topic_id)
    if topic is None or topic.user_id != user_id:
        raise NotFoundError("topic not found")

    # Overview: the summary and overview-kind sections of the topic's compiled page.
    overview: list[Line] = []
    page = (
        db.execute(select(WikiPage).where(WikiPage.user_id == user_id, WikiPage.topic_id == topic.id))
        .scalars()
        .first()
    )
    if page is not None and page.body:
        body = WikiBody.model_validate(page.body)
        sentences = list(body.summary)
        for section in body.sections:
            if section.kind == "overview":
                sentences.extend(section.sentences)
        for sentence in sentences:
            sources = tuple(
                src
                for src in (_claim_source(db, user_id, cid) for cid in sentence.claim_ids)
                if src is not None
            )
            overview.append(Line(text=sentence.text, sources=sources))

    # Conflicts and camps: one section per cluster of this topic that has two or more camps.
    clusters = db.execute(
        select(ClaimCluster)
        .where(ClaimCluster.user_id == user_id, ClaimCluster.topic_id == topic.id)
        .order_by(ClaimCluster.id)
    ).scalars()
    camp_sections: list[Section] = []
    for cluster in clusters:
        claims, relations = cluster_camp_inputs(db, cluster.id)
        if not claims:
            continue
        camps = compute_camps(claims, relations)
        if len(camps.camps) < 2:
            continue
        lines: list[Line] = []
        for camp in camps.camps:
            for claim_id in camp.claim_ids:
                claim = db.get(Claim, claim_id)
                src = _claim_source(db, user_id, claim_id)
                if claim is not None and src is not None:
                    lines.append(Line(text=f"{camp.label}: {claim.statement}", sources=(src,)))
        camp_sections.append(Section(f"Camps: {cluster.label or f'cluster {cluster.id}'}", tuple(lines)))

    # Belief diffs: what changed for this topic's claims in the last 30 days.
    end = clock.utcnow()
    start = end - timedelta(days=BELIEF_WINDOW_DAYS)
    payload = compute_belief_diff(
        db, user_id, start=start, end=end, scope_kind="topic", scope_id=topic.id
    ).payload

    new_lines: list[Line] = []
    for group in payload["new_claims"]:
        for claim in group["claims"]:
            src = _claim_source(db, user_id, claim["id"])
            new_lines.append(
                Line(
                    text=f"{group['label'] or 'claims'}: {claim['statement']}", sources=(src,) if src else ()
                )
            )

    changed_lines: list[Line] = []
    for row in payload["superseded"]:
        old_src = _claim_source(db, user_id, row["old"]["id"])
        sources = [old_src] if old_src else []
        replacement = ""
        if row.get("new"):
            replacement = f" → {row['new']['statement']}"
            new_src = _claim_source(db, user_id, row["new"]["id"])
            if new_src:
                sources.append(new_src)
        changed_lines.append(Line(text=f"~~{row['old']['statement']}~~{replacement}", sources=tuple(sources)))

    conflict_lines: list[Line] = []
    for row in payload["new_conflicts"]:
        sources = tuple(
            src
            for src in (
                _claim_source(db, user_id, row["a"]["id"]),
                _claim_source(db, user_id, row["b"]["id"]),
            )
            if src is not None
        )
        conflict_lines.append(
            Line(text=f"{row['a']['statement']} ⟂ {row['b']['statement']}", sources=sources)
        )

    return render_brief(
        title=f"Topic brief: {topic.label}",
        subtitle=f"Generated {end:%Y-%m-%d} · belief changes over the last {BELIEF_WINDOW_DAYS} days",
        sections=[
            Section("Overview", tuple(overview), empty="No compiled overview yet."),
            *camp_sections,
            Section("Belief changes: new information", tuple(new_lines), empty="Nothing new in this period."),
            Section("Belief changes: updated facts", tuple(changed_lines), empty="No facts were updated."),
            Section(
                "Belief changes: new disagreements", tuple(conflict_lines), empty="No new disagreements."
            ),
        ],
    )


__all__ = ["BELIEF_WINDOW_DAYS", "build_answer_brief", "build_topic_brief"]
