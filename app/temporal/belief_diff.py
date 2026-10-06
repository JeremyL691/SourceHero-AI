"""Belief diff: what changed, over a period, for a scope.

Two time axes, because they answer different questions. The **world** axis asks "what
happened between these dates"; the **observed** axis asks "what did I learn between
these dates" -- which is the honest framing when a Wayback backfill run today inserts a
snapshot from March.

The digest is generated from the payload and every bullet must cite a claim id that is
actually in the payload. A bullet citing anything else is dropped: a summary that
invents its own evidence is worse than no summary.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.claim import Claim, ClaimCluster, ClaimEvent
from app.models.temporal import BeliefDiff

log = get_logger(__name__)

MAX_TOP_CLUSTERS = 20


class NoLiveProvider(Exception):
    """No model is configured, so the digest falls back to the template."""

    code = "no_provider"


@dataclass
class BeliefDiffResult:
    id: uuid.UUID
    period_start: datetime
    period_end: datetime
    scope_kind: str
    scope_id: int | uuid.UUID | None
    payload: dict
    digest_md: str
    digest_method: str
    stats: dict = field(default_factory=dict)


def _claim_payload(claim: Claim) -> dict:
    return {
        "id": claim.id,
        "statement": claim.statement,
        "quote": claim.quote,
        "status": claim.status,
        "document_id": str(claim.document_id),
        "cluster_id": claim.cluster_id,
        "asserted_at": claim.asserted_at.isoformat(),
        "superseded_by": claim.superseded_by,
    }


def _scope_claim_ids(
    db: Session, user_id: uuid.UUID, scope_kind: str, scope_id: int | uuid.UUID | str | None
) -> set[int] | None:
    """Which claims a scope covers, or None for "all of them"."""
    if scope_kind == "cluster" and scope_id is not None:
        cid = (
            int(scope_id)
            if not isinstance(scope_id, uuid.UUID)
            else (scope_id.int if scope_id.int < 2**63 else None)
        )
        if cid is None:
            return set()
        cluster = db.get(ClaimCluster, cid)
        if cluster is None or cluster.user_id != user_id:
            return set()
        return set(
            db.execute(select(Claim.id).where(Claim.user_id == user_id, Claim.cluster_id == cluster.id))
            .scalars()
            .all()
        )

    if scope_kind == "topic" and scope_id is not None:
        tid = (
            int(scope_id)
            if not isinstance(scope_id, uuid.UUID)
            else (scope_id.int if scope_id.int < 2**63 else None)
        )
        if tid is None:
            return set()
        from app.models.wiki import Topic

        topic = db.get(Topic, tid)
        if topic is None or topic.user_id != user_id:
            return set()
        cluster_ids = set(
            db.execute(
                select(ClaimCluster.id).where(ClaimCluster.user_id == user_id, ClaimCluster.topic_id == tid)
            )
            .scalars()
            .all()
        )
        return set(
            db.execute(select(Claim.id).where(Claim.user_id == user_id, Claim.cluster_id.in_(cluster_ids)))
            .scalars()
            .all()
        )

    if scope_kind == "watch" and scope_id is not None:
        from app.claims.cluster import thresholds
        from app.indexing.embeddings import cosine
        from app.models.document import Document
        from app.models.temporal import Watch

        wid = uuid.UUID(str(scope_id)) if not isinstance(scope_id, uuid.UUID) else scope_id
        watch = db.get(Watch, wid)
        if watch is None or watch.user_id != user_id:
            return set()
        if watch.kind == "source" and watch.source_id is not None:
            doc_ids = set(
                db.execute(
                    select(Document.id).where(
                        Document.user_id == user_id, Document.source_id == watch.source_id
                    )
                )
                .scalars()
                .all()
            )
            return set(
                db.execute(select(Claim.id).where(Claim.user_id == user_id, Claim.document_id.in_(doc_ids)))
                .scalars()
                .all()
            )
        if watch.embedding is None:
            return set()
        limit = thresholds()["pair_sim"]
        hits = set()
        for claim in db.execute(
            select(Claim).where(Claim.user_id == user_id, Claim.status == "active")
        ).scalars():
            if cosine(list(watch.embedding), list(claim.embedding or [])) >= limit:
                hits.add(claim.id)
        return hits

    return None


def compute_belief_diff(
    db: Session,
    user_id: uuid.UUID,
    *,
    start: datetime,
    end: datetime,
    scope_kind: str = "all",
    scope_id: int | uuid.UUID | None = None,
    time_axis: str = "world",
) -> BeliefDiffResult:
    """Every claim-level change in `[start, end)`, grouped into readable sections."""
    column = ClaimEvent.observed_at if time_axis == "observed" else ClaimEvent.at
    scoped = _scope_claim_ids(db, user_id, scope_kind, scope_id)

    events = (
        db.execute(
            select(ClaimEvent)
            .where(
                ClaimEvent.user_id == user_id,
                column >= start,
                column < end,
            )
            .order_by(column)
        )
        .scalars()
        .all()
    )
    if scoped is not None:
        events = [e for e in events if e.claim_id in scoped]

    by_id = {e.claim_id: e for e in events}
    claims = {}
    for claim_id in by_id:
        claim = db.get(Claim, claim_id)
        if claim is not None and (scoped is None or claim_id in scoped):
            claims[claim_id] = claim

    payload: dict = {"new_claims": [], "superseded": [], "retracted": [], "new_conflicts": [], "resolved": []}

    # --- new claims, grouped by cluster and ranked by how much it matters
    new_by_cluster: dict[int | None, list[Claim]] = {}
    for event in events:
        if event.event != "asserted":
            continue
        claim = claims.get(event.claim_id)
        if claim is not None:
            new_by_cluster.setdefault(claim.cluster_id, []).append(claim)

    cluster_meta: dict[int, dict] = {}
    ranked: list[tuple[float, int | None]] = []
    for cluster_id, members in new_by_cluster.items():
        documents = {m.document_id for m in members}
        conflicts = 0
        label = ""
        if cluster_id is not None:
            cluster = db.get(ClaimCluster, cluster_id)
            if cluster is not None:
                conflicts = cluster.conflict_count
                label = cluster.label
        cluster_meta[cluster_id] = {"label": label, "size": len(members), "documents": len(documents)}
        # Size weighted by spread across sources, plus a bonus for being contested:
        # a claim contradicted by three sources matters more than three agreeing ones.
        score = len(members) * math.log(1 + len(documents)) + 2 * conflicts
        ranked.append((score, cluster_id))
    ranked.sort(reverse=True)

    for _, cluster_id in ranked[:MAX_TOP_CLUSTERS]:
        payload["new_claims"].append(
            {
                "cluster_id": cluster_id,
                "label": cluster_meta[cluster_id]["label"],
                "claims": [_claim_payload(c) for c in new_by_cluster[cluster_id]],
            }
        )

    # --- supersessions and retractions
    for event in events:
        if event.event == "superseded":
            old = claims.get(event.claim_id)
            new = db.get(Claim, event.related_claim_id) if event.related_claim_id else None
            if old is not None:
                payload["superseded"].append(
                    {
                        "old": _claim_payload(old),
                        "new": _claim_payload(new) if new is not None else None,
                    }
                )
        elif event.event == "retracted":
            claim = claims.get(event.claim_id)
            if claim is not None:
                payload["retracted"].append({"claim": _claim_payload(claim)})

    # --- conflicts opened and resolved in the period
    seen_pairs: set[tuple[int, int]] = set()
    for event in events:
        if event.event != "conflict_opened" or event.related_claim_id is None:
            continue
        a, b = sorted((event.claim_id, event.related_claim_id))
        if (a, b) in seen_pairs:
            continue
        seen_pairs.add((a, b))
        claim_a = db.get(Claim, a)
        claim_b = db.get(Claim, b)
        if claim_a is None or claim_b is None:
            continue
        if scoped is not None and a not in scoped and b not in scoped:
            continue
        payload["new_conflicts"].append({"a": _claim_payload(claim_a), "b": _claim_payload(claim_b)})

    for event in events:
        if event.event != "conflict_resolved" or event.related_claim_id is None:
            continue
        claim = db.get(Claim, event.claim_id)
        other = db.get(Claim, event.related_claim_id)
        if claim is None or other is None:
            continue
        payload["resolved"].append(
            {"a": _claim_payload(claim), "b": _claim_payload(other), "cause": event.meta.get("cause", "")}
        )

    # --- the user's own notes that have been overtaken
    from app.temporal.staleness import detect_stale_notes

    all_stale = detect_stale_notes(db, user_id, since=start)
    if scoped is not None:
        all_stale = [n for n in all_stale if n.claim_id in scoped or any(e.id in scoped for e in n.evidence)]
    payload["stale_notes"] = [note.as_dict() for note in all_stale]

    payload["stats"] = {
        "new_claims": sum(len(g["claims"]) for g in payload["new_claims"]),
        "clusters": len(payload["new_claims"]),
        "superseded": len(payload["superseded"]),
        "retracted": len(payload["retracted"]),
        "new_conflicts": len(payload["new_conflicts"]),
        "resolved": len(payload["resolved"]),
        "stale_notes": len(payload["stale_notes"]),
    }

    digest, method = render_digest(payload, start=start, end=end)
    return BeliefDiffResult(
        id=uuid.uuid4(),
        period_start=start,
        period_end=end,
        scope_kind=scope_kind,
        scope_id=scope_id,
        payload=payload,
        digest_md=digest,
        digest_method=method,
        stats=payload["stats"],
    )


def compute_and_store_diff(
    db: Session,
    user_id: uuid.UUID,
    *,
    start: datetime,
    end: datetime,
    scope_kind: str = "all",
    scope_id: int | uuid.UUID | None = None,
    time_axis: str = "world",
) -> BeliefDiff:
    result = compute_belief_diff(
        db,
        user_id,
        start=start,
        end=end,
        scope_kind=scope_kind,
        scope_id=scope_id,
        time_axis=time_axis,
    )
    stored_scope_id: uuid.UUID | None = None
    if scope_id is not None:
        if isinstance(scope_id, uuid.UUID):
            stored_scope_id = scope_id
        elif isinstance(scope_id, int):
            stored_scope_id = uuid.UUID(int=scope_id)
        else:
            try:
                stored_scope_id = uuid.UUID(str(scope_id))
            except ValueError:
                stored_scope_id = uuid.UUID(int=int(scope_id))
    row = BeliefDiff(
        id=result.id,
        user_id=user_id,
        scope_kind=scope_kind,
        scope_id=stored_scope_id,
        period_start=start,
        period_end=end,
        payload=result.payload,
        digest_md=result.digest_md,
        digest_method=result.digest_method,
    )
    db.add(row)
    db.flush()
    return row


def render_digest(payload: dict, *, start: datetime, end: datetime) -> tuple[str, str]:
    """The Markdown summary a reader actually reads.

    Tries the model first and keeps its bullets only if every one cites a claim id that
    appears in the payload; otherwise falls back to the template, which cannot invent
    anything because it is generated from the payload directly.
    """
    try:
        return _llm_digest(payload, start=start, end=end)
    except Exception as exc:  # noqa: BLE001 - a digest must never fail the request
        log.info("belief diff digest fell back to the template: %s", exc)
        return template_digest(
            payload, start=start, end=end
        ), f"template(fallback:{getattr(exc, 'code', type(exc).__name__)})"


def build_diff_digest_context(payload: dict, *, start: datetime, end: datetime) -> dict:
    return {
        "start": start.date().isoformat(),
        "end": end.date().isoformat(),
        "payload_text": _payload_text(payload),
    }


def _llm_digest(payload: dict, *, start: datetime, end: datetime) -> tuple[str, str]:
    from app.llm.client import get_llm
    from app.llm.prompts import render
    from app.llm.schemas import DiffDigest
    from app.llm.types import LLMRequest

    client = get_llm()
    if client.mode != "live":
        raise NoLiveProvider("no live provider configured; using the template digest")

    known = collect_claim_ids(payload)
    ctx = build_diff_digest_context(payload, start=start, end=end)
    messages, version = render(
        "diff_digest",
        schema=DiffDigest,
        **ctx,
    )
    result = client.complete(
        LLMRequest(
            task="diff_digest",
            messages=messages,
            tier="smart",
            schema=DiffDigest,
            prompt_version=version,
        )
    )
    assert result.parsed is not None

    # Drop any bullet citing a claim the payload does not contain.
    kept = [b for b in result.parsed.bullets if b.claim_ids and all(c in known for c in b.claim_ids)]
    if not kept:
        raise ValueError("every proposed bullet cited an unknown claim")
    return "\n".join(
        f"- {_clean(b.text)} {''.join(f'[c:{c}]' for c in b.claim_ids)}" for b in kept
    ), f"llm:{result.model}"


def _clean(text: str) -> str:
    return text.strip().rstrip(".")


def collect_claim_ids(payload: dict) -> set[int]:
    ids: set[int] = set()

    def walk(node) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "id" and isinstance(value, int):
                    ids.add(value)
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload.get("new_claims", []))
    walk(payload.get("superseded", []))
    walk(payload.get("retracted", []))
    walk(payload.get("new_conflicts", []))
    walk(payload.get("resolved", []))
    walk(payload.get("stale_notes", []))
    return ids


def _payload_text(payload: dict) -> str:
    import json

    return json.dumps(payload, ensure_ascii=False, default=str)


def template_digest(payload: dict, *, start: datetime, end: datetime) -> str:
    """The offline digest. Every line is generated from the payload, so it cannot
    assert anything the payload does not contain."""
    lines = [f"## What changed · {start:%b %d} – {end:%b %d}"]

    if payload.get("superseded"):
        lines.append("### Updated facts")
        for row in payload["superseded"]:
            new = row.get("new")
            replacement = f" → **{new['statement']}** [c:{new['id']}]" if new else ""
            lines.append(f"- ~~{row['old']['statement']}~~{replacement}")

    if payload.get("new_conflicts"):
        lines.append("### New disagreements")
        for row in payload["new_conflicts"]:
            lines.append(
                f"- {row['a']['statement']} ⟂ {row['b']['statement']} [c:{row['a']['id']}] [c:{row['b']['id']}]"
            )

    if payload.get("stale_notes"):
        lines.append("### Your notes that may be outdated")
        for note in payload["stale_notes"]:
            reasons = "; ".join(note["reasons"])
            evidence = "".join(f" [c:{e['claim_id']}]" for e in note["evidence"])
            lines.append(f'- "{note["statement"]}" — {reasons}{evidence}')

    if payload.get("new_claims"):
        lines.append("### New information")
        for group in payload["new_claims"]:
            label = group.get("label") or "claims"
            first = group["claims"][0]
            lines.append(f"- **{label}**: {first['statement']} [c:{first['id']}]")

    if len(lines) == 1:
        lines.append("### Nothing changed in this period.")
    return "\n".join(lines)


__all__ = [
    "MAX_TOP_CLUSTERS",
    "BeliefDiffResult",
    "collect_claim_ids",
    "compute_and_store_diff",
    "compute_belief_diff",
    "render_digest",
    "template_digest",
]
