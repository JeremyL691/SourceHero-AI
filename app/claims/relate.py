"""Relation judging.

Two stages, and the order is the point. The heuristic runs first and decides the
clear cases -- a same-unit numeric conflict, an asymmetric negation, a rescheduled
date. Only pairs the heuristic could not settle are sent to the model, in batches. A
live LLM therefore costs a fraction of a call per pair, and the offline path is
exactly as good as it was.

The bound that keeps this usable is `MAX_JUDGE_PAIRS_PER_CLAIM`: each new claim
compares against at most that many candidates, found by embedding search, so judging
cost is O(1) in the size of the ledger.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.claims.cluster import thresholds
from app.claims.numbers import extract_quantities
from app.claims.signals import (
    date_conflict,
    negation_asymmetry,
    numbers_conflict,
    numbers_differ,
    temporal_update_cue,
    token_f1,
)
from app.core import time as clock
from app.core.logging import get_logger
from app.indexing.tokenize import search_tokens
from app.llm.providers.fake import register_default
from app.models.claim import Claim, ClaimEvent, ClaimRelation

log = get_logger(__name__)

MAX_JUDGE_PAIRS_PER_CLAIM = 5
MIN_F1_FOR_RELATION = 0.20

RELATIONS = ("supports", "contradicts", "refines", "supersedes", "duplicate")

# Sources whose claims stay `active` even when contradicted or superseded: the user's
# own notes. Changing a note's status would edit the user's writing; M9 flags them as
# potentially outdated instead.
NOTE_SOURCE_TYPES = ("note", "clip")


@dataclass
class RelationDraft:
    relation: str
    direction: str | None
    confidence: float
    signals: dict = field(default_factory=dict)
    rationale: str = ""


@register_default("relation_judge")
def _fake_relation_judge(context: dict) -> dict:
    pairs = context.get("pairs", [])
    results = []
    for p in pairs:
        results.append(
            {
                "pair_index": p.get("index", 1),
                "relation": "unrelated",
                "direction": None,
                "confidence": 0.5,
                "rationale": "heuristic best guess",
            }
        )
    return {"results": results}


def build_relation_judge_context(pairs: list[dict]) -> dict:
    return {"pairs": pairs}


def candidate_pairs(
    db: Session, claim: Claim, *, limit: int = MAX_JUDGE_PAIRS_PER_CLAIM
) -> list[tuple[Claim, float]]:
    """The claims most worth comparing `claim` against.

    Cross-document only: two versions of the same page are the same lineage, and that
    is the temporal reconcile's job (M9), not cross-source relation judging.
    Uses pgvector kNN in SQL to ensure cross-document candidates are retrieved even
    when the source document has many claims.
    """
    if claim.embedding is None:
        return []

    limit_sim = thresholds()["pair_sim"]
    vector = list(claim.embedding)

    stmt = (
        select(Claim, (1.0 - Claim.embedding.cosine_distance(vector)).label("sim"))
        .where(Claim.user_id == claim.user_id)
        .where(Claim.status.in_(("active", "carried")))
        .where(Claim.document_id != claim.document_id)
        .where(Claim.embedding.is_not(None))
        .order_by(Claim.embedding.cosine_distance(vector))
        .limit(limit)
    )
    rows = db.execute(stmt).all()
    out: list[tuple[Claim, float]] = []
    for c, sim in rows:
        sim_val = float(sim)
        if sim_val >= limit_sim:
            out.append((c, sim_val))
    return out


# A value starts at a digit that is not part of a name: the "2" in "HX-2" is not a number.
_NUMBER = re.compile(r"(?<![\w-])[$€£]?\d[\d,.]*%?")
_MONTHS = frozenset(
    [
        "january",
        "february",
        "march",
        "april",
        "may",
        "june",
        "july",
        "august",
        "september",
        "october",
        "november",
        "december",
    ]
)
# Time and range qualifiers. A qualifier on one side only changes what a claim says.
_QUALIFIERS = tuple(
    (tag, re.compile(pattern, re.IGNORECASE))
    for tag, pattern in (
        ("first-year", r"\bfirst[- ]year\b|首年|第一年"),
        ("quarter", r"\bq[1-4]\b|第[一二三四]季度|每季度"),
        ("year", r"\beach year\b|\bevery year\b|\bper year\b|\bannually\b|每年"),
        ("month", r"\bmonthly\b|\bper month\b|每月"),
    )
)


def _text(claim: Claim) -> str:
    return getattr(claim, "statement", None) or ""


def _field(claim: Claim, name: str) -> str:
    """An explicit subject or predicate, used only when it names an entity.

    The extractor falls back to the first lowercase content words when no entity is
    present ("congestion charge reduce"), and those say nothing about the subject.
    """
    value = (getattr(claim, name, None) or "").strip()
    if not any(c.isupper() or c.isdigit() for c in value):
        return ""
    return value.lower()


_YEAR = re.compile(r"\d{4}")
_CN_DATE_UNIT = re.compile(r"\s*(?:月|日|号|年|季度)")
_CLAUSE_BREAK = re.compile(r"[,;.，；。]")
_WORD = re.compile(r"[a-z]+")


def _is_date_value(statement: str, match: re.Match[str]) -> bool:
    """A year, a Chinese date unit after the number, or an English month in its clause.

    All three mark a number as a date, which the date checks own. Only the metric
    slot rule below is concerned with values, so the date handling lives in this helper.
    """
    digits = match.group().lstrip("$€£").rstrip("%")
    if _YEAR.fullmatch(digits) and 1900 <= int(digits) <= 2099:
        return True
    if _CN_DATE_UNIT.match(statement, match.end()):
        return True
    clause = _CLAUSE_BREAK.split(statement[: match.start()])[-1]
    return any(word in _MONTHS for word in _WORD.findall(clause.lower()))


def _slot(statement: str) -> set[str]:
    """The last two content words before the first metric value; empty when there is none."""
    for match in _NUMBER.finditer(statement):
        if _is_date_value(statement, match):
            continue
        words = [t for t in search_tokens(statement[: match.start()]) if not t.isdigit() and t not in _MONTHS]
        return set(words[-2:])
    return set()


# A comma or semicolon ends the clause a qualifier belongs to. Decimal points are not breaks.
_QUALIFIER_CLAUSE_BREAK = re.compile(r"[,;，；。]")


def _main_clause(statement: str) -> str:
    """The statement up to its first clause break.

    "..., so we should budget for it in Q4" puts the quarter on a different action from the
    claim, so the qualifier must not be read from the subordinate clause.
    """
    return _QUALIFIER_CLAUSE_BREAK.split(statement, maxsplit=1)[0] or statement


def _count_head(statement: str) -> str:
    """The noun a count is of ("120 rooms" gives "room"); empty when there is no count."""
    for quantity in extract_quantities(statement):
        if quantity.dimension == "count" and quantity.head:
            return quantity.head.lower().rstrip("s")
    return ""


def _qualifier_tags(statement: str) -> frozenset[str]:
    return frozenset(tag for tag, pattern in _QUALIFIERS if pattern.search(_main_clause(statement)))


def _defer_to_llm(a: Claim, b: Claim) -> bool:
    """True when the pair is about different things, or only one side is qualified.

    Explicit subject and predicate fields are compared when both claims carry them.
    Otherwise the metric slot is compared: the words before each side's first value.
    When both sides have a value and each slot has a word the other lacks, the two
    statements measure different things. Ceiling: no POS tagging, so the slot can
    cut a multi-word name in half. Upgrade path: a noun-phrase chunker.
    """
    filled = {name: (_field(a, name), _field(b, name)) for name in ("subject", "predicate")}
    for left, right in filled.values():
        if left and right and left != right:
            return True
    if _qualifier_tags(_text(a)) != _qualifier_tags(_text(b)):
        return True
    subject_a, subject_b = filled["subject"]
    if subject_a and subject_b:
        return False

    count_a, count_b = _count_head(_text(a)), _count_head(_text(b))
    if count_a and count_b and count_a != count_b:
        return True  # "120 rooms" against "120 bathrooms": the counted noun is the metric

    slot_a, slot_b = _slot(_text(a)), _slot(_text(b))
    if not slot_a or not slot_b:
        return False
    return bool(slot_a - slot_b) and bool(slot_b - slot_a)


def heuristic_relation(a: Claim, b: Claim, similarity: float) -> RelationDraft | None:
    """Decide a pair from surface signals alone, or return None to defer to the LLM."""
    if _defer_to_llm(a, b):
        return None

    limits = thresholds()
    numbers = numbers_conflict(a.statement, b.statement)
    dates = date_conflict(a.statement, b.statement, anchor_a=a.asserted_at, anchor_b=b.asserted_at)
    negation = negation_asymmetry(a.statement, b.statement)
    overlap = token_f1(a.statement, b.statement)

    signals = {
        "nc": numbers,
        "dc": dates,
        "neg": negation,
        "f1": round(overlap, 4),
        "sim": round(similarity, 4),
    }

    num_diff = numbers_differ(a.statement, b.statement)
    conflicting = numbers is True or num_diff or dates is True or negation
    if conflicting and overlap >= MIN_F1_FOR_RELATION:
        if a.asserted_at is not None and b.asserted_at is not None:
            newer, older = order_by_asserted_at(a, b)
            rescheduled = (dates is True or numbers is True or num_diff) and temporal_update_cue(
                newer.statement
            )
            if rescheduled and newer.asserted_at > older.asserted_at:
                return RelationDraft(
                    relation="supersedes",
                    direction="a_to_b" if newer.id == a.id else "b_to_a",
                    confidence=0.65,
                    signals=signals,
                    rationale="the newer claim signals an update to the older one",
                )
        fired = sum(1 for flag in (numbers or num_diff, dates, negation) if flag is True)
        return RelationDraft(
            relation="contradicts",
            direction=None,
            confidence=0.6 + 0.1 * (fired - 1),
            signals=signals,
            rationale="the two statements cannot both be true",
        )

    if similarity >= limits["dup_sim"] and numbers is not True and not num_diff:
        return RelationDraft("duplicate", None, 0.7, signals, "near-identical statements")

    if overlap >= 0.5 and numbers is not True and not num_diff and dates is not True and not negation:
        return RelationDraft(
            relation="supports",
            direction=None,
            confidence=0.55 + 0.2 * (overlap - 0.5),
            signals=signals,
            rationale="the statements agree",
        )

    return None


def order_by_asserted_at(a: Claim, b: Claim) -> tuple[Claim, Claim]:
    """(newer, older). Ties break on id so the order is stable."""
    if a.asserted_at is None and b.asserted_at is None:
        return (a, b) if (a.id or 0) >= (b.id or 0) else (b, a)
    if a.asserted_at is None:
        return b, a
    if b.asserted_at is None:
        return a, b
    if (a.asserted_at, a.id or 0) >= (b.asserted_at, b.id or 0):
        return a, b
    return b, a


def store_relation(
    db: Session, user_id: uuid.UUID, a: Claim, b: Claim, draft: RelationDraft, *, method: str
) -> ClaimRelation | None:
    """Upsert a relation, ordered by id so the pair has one canonical orientation.

    Returns None when the relation already exists and a human has labelled it: a user
    decision outranks anything the pipeline re-derives.
    """
    if a.id == b.id:
        return None
    first, second = (a, b) if a.id < b.id else (b, a)

    existing = db.execute(
        select(ClaimRelation).where(
            ClaimRelation.user_id == user_id,
            ClaimRelation.a_claim_id == first.id,
            ClaimRelation.b_claim_id == second.id,
        )
    ).scalar_one_or_none()

    if existing is not None and existing.user_label is not None:
        return existing

    if existing is None:
        existing = ClaimRelation(user_id=user_id, a_claim_id=first.id, b_claim_id=second.id)
        db.add(existing)
    existing.relation = draft.relation
    # Direction is stored against the canonical (a, b) ordering.
    if draft.direction is None:
        existing.direction = None
    elif first is a:
        existing.direction = draft.direction
    else:
        existing.direction = "b_to_a" if draft.direction == "a_to_b" else "a_to_b"
    existing.confidence = draft.confidence
    existing.method = method
    existing.signals = draft.signals
    existing.rationale = draft.rationale
    db.flush()
    return existing


LLM_RELATION_BATCH_SIZE = 8


def _source_name_for_claim(db: Session, claim: Claim) -> str:
    from app.models.document import Document
    from app.models.source import Source

    doc = db.get(Document, claim.document_id)
    if doc is None:
        return "Unknown"
    source = db.get(Source, doc.source_id)
    return source.name if source and source.name else (doc.title or "Unknown")


def judge_pairs_batch(
    client,
    batch: list[tuple],
    *,
    source_of,
    user_id: uuid.UUID | None = None,
) -> dict[int, RelationDraft | None]:
    """One relation-judge call over up to a batch of (a, b, similarity) pairs.

    Returns the pairs the model answered, keyed by their position in `batch`: a draft for
    a relation, or None when the model said unrelated. Pairs it did not answer are absent.
    `source_of` names the source of a claim; the product passes the document title, the
    evaluation passes a neutral label. The database is not touched here, so the product
    path and the evaluation runner send the same prompt and read the same answer.
    """
    from app.llm.prompts import render
    from app.llm.schemas import RelationResponse
    from app.llm.types import LLMRequest

    pairs_payload = [
        {
            "index": idx + 1,
            "a": {
                "source": source_of(a),
                "date": a.asserted_at.date().isoformat() if a.asserted_at else "unknown",
                "statement": a.statement,
                "quote": a.quote,
            },
            "b": {
                "source": source_of(b),
                "date": b.asserted_at.date().isoformat() if b.asserted_at else "unknown",
                "statement": b.statement,
                "quote": b.quote,
            },
        }
        for idx, (a, b, _sim) in enumerate(batch)
    ]
    ctx = build_relation_judge_context(pairs_payload)
    messages, version = render("relation_judge", schema=RelationResponse, **ctx)
    res = client.complete(
        LLMRequest(
            task="relation_judge",
            messages=messages,
            tier="fast",
            schema=RelationResponse,
            prompt_version=version,
            max_tokens=6000,  # a batch of pairs with rationales; DECISIONS 2026-10-12 K3.5
            user_id=user_id,
            context=ctx,
        )
    )
    judged: dict[int, RelationDraft | None] = {}
    if res.parsed is not None and isinstance(res.parsed, RelationResponse):
        for r in res.parsed.results:
            if not 1 <= r.pair_index <= len(batch):
                continue
            _a, _b, sim = batch[r.pair_index - 1]
            if r.relation == "unrelated":
                judged[r.pair_index - 1] = None
                continue
            judged[r.pair_index - 1] = RelationDraft(
                relation=r.relation,
                direction=r.direction,
                confidence=r.confidence or 0.6,
                signals={"llm": True, "sim": round(sim, 4)},
                rationale=r.rationale or "llm relation judge",
            )
    return judged


def judge_relations(db: Session, claim_ids: list[int]) -> dict:
    """Judge every candidate pair for the given claims."""
    stats = {"pairs": 0, "contradicts": 0, "supersedes": 0, "supports": 0, "duplicate": 0, "deferred": 0}
    if not claim_ids:
        return stats

    claims = list(db.execute(select(Claim).where(Claim.id.in_(claim_ids))).scalars())
    if not claims:
        return stats
    user_id = claims[0].user_id
    seen: set[tuple[int, int]] = set()
    new_relations: list[ClaimRelation] = []
    deferred_pairs: list[tuple[Claim, Claim, float]] = []

    for claim in claims:
        for other, similarity in candidate_pairs(db, claim):
            key = (min(claim.id, other.id), max(claim.id, other.id))
            if key in seen:
                continue
            seen.add(key)

            draft = heuristic_relation(claim, other, similarity)
            if draft is None:
                deferred_pairs.append((claim, other, similarity))
                continue

            stored = store_relation(db, claim.user_id, claim, other, draft, method="heuristic")
            if stored is None:
                continue
            new_relations.append(stored)
            stats["pairs"] += 1
            stats[draft.relation] = stats.get(draft.relation, 0) + 1
            _apply_side_effects(db, claim, other, stored)

    if deferred_pairs:
        from app.llm.client import get_llm

        client = get_llm(db=db, user_id=user_id)
        if client.effective.provider != "heuristic":
            for start_idx in range(0, len(deferred_pairs), LLM_RELATION_BATCH_SIZE):
                batch = deferred_pairs[start_idx : start_idx + LLM_RELATION_BATCH_SIZE]
                try:
                    judged = judge_pairs_batch(
                        client,
                        batch,
                        source_of=lambda claim: _source_name_for_claim(db, claim),
                        user_id=user_id,
                    )
                    for position, draft in judged.items():
                        if draft is None:
                            continue
                        a, b, _sim = batch[position]
                        stored = store_relation(db, user_id, a, b, draft, method="llm")
                        if stored is not None:
                            new_relations.append(stored)
                            stats["pairs"] += 1
                            stats[draft.relation] = stats.get(draft.relation, 0) + 1
                            _apply_side_effects(db, a, b, stored)
                    unjudged_count = len(batch) - len(judged)
                    if unjudged_count > 0:
                        stats["deferred"] += unjudged_count
                except Exception as exc:
                    log.warning("LLM relation judging failed, keeping pairs deferred: %s", exc)
                    stats["deferred"] += len(batch)
        else:
            stats["deferred"] += len(deferred_pairs)

    _merge_across_clusters(db, user_id, new_relations, stats)
    db.flush()
    return stats


CLUSTER_MERGE_MIN_CONF = 0.75


def merge_confidence() -> float:
    """How confident a relation must be before it is allowed to merge two clusters.

    A weak relation must not be able to fuse two genuinely different topics: the
    cost of a wrong merge is that every future claim about either one joins the same
    camp. Default is 0.75 (higher than heuristic contradiction's 0.6).
    """
    from app.core.config import get_settings

    return float(
        getattr(get_settings(), "merge_confidence", CLUSTER_MERGE_MIN_CONF) or CLUSTER_MERGE_MIN_CONF
    )


def _merge_across_clusters(
    db: Session, user_id: uuid.UUID, relations: list[ClaimRelation], stats: dict
) -> None:
    """Fuse clusters whose claims are tied by a strong relation.

    Relation judging finds contradictions by claim-to-claim similarity; the two claims
    can land in different clusters, and then the cluster view cannot show the
    disagreement at all. A relation confident enough to assert "these are about the
    same thing and disagree" is also evidence the clusters belong together.
    """
    from app.claims.cluster import merge_clusters
    from app.models.claim import Claim, ClaimCluster

    limit = merge_confidence()
    merged = 0
    for relation in relations:
        if relation.user_id != user_id:
            continue
        if relation.user_label == "rejected":
            continue
        is_confident = (relation.confidence >= limit) or (relation.method in ("llm", "user"))
        if not is_confident:
            continue
        a = db.get(Claim, relation.a_claim_id)
        b = db.get(Claim, relation.b_claim_id)
        if a is None or b is None:
            continue
        if a.user_id != user_id or b.user_id != user_id:
            continue
        if a.cluster_id is None or b.cluster_id is None or a.cluster_id == b.cluster_id:
            continue
        keep_row = db.get(ClaimCluster, a.cluster_id)
        drop_row = db.get(ClaimCluster, b.cluster_id)
        if keep_row is None or drop_row is None:
            continue
        # Merge into the older cluster so ids stay stable across runs.
        keep, drop = (keep_row, drop_row) if keep_row.id <= drop_row.id else (drop_row, keep_row)
        merge_clusters(db, keep, drop)
        merged += 1

    if merged:
        stats["clusters_merged"] = merged


def _source_type_of(db: Session, claim: Claim) -> str:
    from app.models.document import Document
    from app.models.source import Source

    document = db.get(Document, claim.document_id)
    if document is None:
        return ""
    source = db.get(Source, document.source_id)
    return source.source_type if source else ""


def _apply_side_effects(db: Session, a: Claim, b: Claim, relation: ClaimRelation) -> None:
    """Write the events and status changes a newly found relation implies."""
    if relation.relation == "contradicts" and relation.resolved_at is None:
        _open_conflict(db, a, b)
    elif relation.relation == "supersedes":
        claim_a = a if a.id == relation.a_claim_id else b
        claim_b = b if b.id == relation.b_claim_id else a
        newer = claim_a if relation.direction == "a_to_b" else claim_b
        older = claim_b if newer is claim_a else claim_a
        if _source_type_of(db, older) not in NOTE_SOURCE_TYPES:
            apply_supersede(db, older, newer)


def _open_conflict(db: Session, a: Claim, b: Claim) -> None:
    now = clock.utcnow()
    when = max(a.asserted_at, b.asserted_at)
    for claim, other in ((a, b), (b, a)):
        exists = db.execute(
            select(ClaimEvent).where(
                ClaimEvent.claim_id == claim.id,
                ClaimEvent.event == "conflict_opened",
                ClaimEvent.related_claim_id == other.id,
            )
        ).scalar_one_or_none()
        if exists is None:
            db.add(
                ClaimEvent(
                    user_id=claim.user_id,
                    claim_id=claim.id,
                    event="conflict_opened",
                    at=when,
                    observed_at=now,
                    related_claim_id=other.id,
                )
            )
    from app.claims.cluster import recount_conflicts

    for claim in (a, b):
        if claim.cluster_id is not None:
            recount_conflicts(db, claim.cluster_id)
    db.flush()


def apply_supersede(db: Session, older: Claim, newer: Claim) -> None:
    """Mark `older` superseded and close the conflicts it was party to."""
    now = clock.utcnow()
    if older.status == "superseded":
        return
    older.status = "superseded"
    older.superseded_by = newer.id
    older.valid_to = newer.asserted_at
    db.flush()

    db.add(
        ClaimEvent(
            user_id=older.user_id,
            claim_id=older.id,
            event="superseded",
            at=newer.asserted_at,
            observed_at=now,
            related_claim_id=newer.id,
        )
    )
    resolve_conflicts_for(db, older)


def resolve_conflicts_for(db: Session, claim: Claim) -> int:
    """Close every unresolved contradiction involving `claim`, and say so."""
    now = clock.utcnow()
    relations = (
        db.execute(
            select(ClaimRelation).where(
                ClaimRelation.user_id == claim.user_id,
                ClaimRelation.relation == "contradicts",
                ClaimRelation.resolved_at.is_(None),
                (ClaimRelation.a_claim_id == claim.id) | (ClaimRelation.b_claim_id == claim.id),
            )
        )
        .scalars()
        .all()
    )

    for relation in relations:
        relation.resolved_at = now
        db.add(
            ClaimEvent(
                user_id=claim.user_id,
                claim_id=claim.id,
                event="conflict_resolved",
                at=now,
                observed_at=now,
                related_claim_id=(
                    relation.b_claim_id if relation.a_claim_id == claim.id else relation.a_claim_id
                ),
                meta={"cause": "claim superseded"},
            )
        )

    from app.claims.cluster import recount_conflicts

    if claim.cluster_id is not None:
        recount_conflicts(db, claim.cluster_id)
    db.flush()
    return len(relations)


def record_assertion(db: Session, claim: Claim, *, at: datetime) -> None:
    """The `asserted` event every new claim emits."""
    db.add(
        ClaimEvent(
            user_id=claim.user_id,
            claim_id=claim.id,
            event="asserted",
            at=at,
            observed_at=clock.utcnow(),
            version_id=claim.version_id,
        )
    )


__all__ = [
    "MAX_JUDGE_PAIRS_PER_CLAIM",
    "merge_confidence",
    "NOTE_SOURCE_TYPES",
    "RELATIONS",
    "RelationDraft",
    "apply_supersede",
    "candidate_pairs",
    "heuristic_relation",
    "judge_relations",
    "order_by_asserted_at",
    "record_assertion",
    "resolve_conflicts_for",
    "store_relation",
]
