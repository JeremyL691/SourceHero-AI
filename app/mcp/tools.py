"""Agent-facing tools, shared by the REST API and the MCP server.

Every tool here is provenance-first: a result an agent cannot trace is a result an
agent cannot use. Each hit therefore carries title, URL, date, the quoted text and its
character offsets into the source document, so an agent can point at the exact span.

These are plain functions of `(db, user, **kwargs) -> dict`, deliberately free of MCP
types -- the server layer is a thin adapter, and the tests can call them directly.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.claims.camps import cluster_camp_inputs, compute_camps
from app.core import time as clock
from app.core.errors import DomainValidationError, NotFoundError, PermissionDeniedError
from app.ingestion.canonical import canonicalize_url
from app.models.chunk import Chunk
from app.models.claim import Claim, ClaimRelation
from app.models.document import Document, DocumentVersion
from app.models.source import Source
from app.models.wiki import WikiPage

MAX_CONTEXT_CHARS = 400
MAX_DIFF_DAYS = 90


def _user_id(user) -> uuid.UUID:
    if isinstance(user, uuid.UUID):
        return user
    return user.id


def _date(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    return str(value)


def _parse_since(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise DomainValidationError(f"could not parse a date from {value!r}", code="bad_date") from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def search_sources(
    db: Session,
    user,
    *,
    query: str,
    k: int = 8,
    source_types: list[str] | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict:
    """Search the user's sources.

    Use this to find what a corpus says about a topic before asking a question about
    it. `query` is plain text; `k` is how many passages to return (1-30);
    `source_types` filters by kind (webpage, rss, pdf, file, note, clip).
    `date_from`/`date_to` are ISO dates. Every hit includes the quoted span and its
    offsets in the source document.
    """
    from app.retrieval.hybrid import hybrid_search
    from app.retrieval.types import SearchFilters

    filters = SearchFilters(
        source_types=source_types or None,
        date_from=_parse_since(date_from),
        date_to=_parse_since(date_to),
    )
    result = hybrid_search(db, _user_id(user), query, k=max(1, min(k, 30)), filters=filters)
    return {
        "query": query,
        "mode": result.mode_effective,
        "count": len(result.hits),
        "hits": [_hit(h) for h in result.hits],
    }


def _hit(hit) -> dict:
    return {
        "chunk_id": hit.chunk_id,
        "document_id": str(hit.document_id),
        "version_id": hit.version_id,
        "title": hit.title,
        "url": hit.url,
        "source": hit.source_name,
        "date": _date(hit.published_at),
        "text": hit.text,
        "char_start": hit.char_start,
        "char_end": hit.char_end,
        "page": hit.page,
        "scores": hit.scores,
    }


def get_passage(db: Session, user, *, chunk_id: int, context_chars: int = MAX_CONTEXT_CHARS) -> dict:
    """Read one passage with its surrounding text.

    Use after `search_sources` when a hit is relevant but the sentence that matters
    may sit just outside it. `chunk_id` comes from a search hit; `context_chars` is how
    much text to show on each side (0-4000). Returns the passage, the surrounding
    text, and the passage's offsets in the full document.
    """
    owner = _user_id(user)
    chunk = db.get(Chunk, chunk_id)
    if chunk is None or chunk.user_id != owner:
        raise NotFoundError("chunk not found")

    version = db.get(DocumentVersion, chunk.version_id)
    document = db.get(Document, chunk.document_id)
    text = version.clean_text if version else ""

    start = max(0, chunk.char_start - max(0, context_chars))
    end = min(len(text), chunk.char_end + max(0, context_chars))
    source = db.get(Source, document.source_id) if document else None
    return {
        "chunk_id": chunk.id,
        "title": document.title if document else "",
        "url": document.url if document else None,
        "source": source.name if source else "",
        "date": _date(document.published_at) if document else None,
        "passage": chunk.text,
        "passage_char_start": chunk.char_start,
        "passage_char_end": chunk.char_end,
        "context": text[start:end],
        "context_char_start": start,
        "context_char_end": end,
        "document_length": len(text),
    }


def ask(db: Session, user, *, question: str, k: int = 8) -> dict:
    """Answer a question from the user's sources, with a verdict per sentence.

    Use for "what do my sources say about X" when the answer needs several passages
    combined. Each sentence comes back with a verdict (supported, contradicted,
    disputed, unsupported) and the evidence it was checked against, so a disagreement
    between sources is visible rather than smoothed over.
    """
    from app.verify.answer import answer_json

    return answer_json(question, _user_id(user), db, k=max(1, min(k, 30)))


def verify(db: Session, user, *, text: str) -> dict:
    """Check sentences against the user's evidence.

    Use when you have written a specific claim and want to know whether the sources
    support it. `text` is one or more sentences; the result gives each sentence a
    verdict plus the evidence that decided it.
    """
    from app.verify.verifier import verify_text

    return verify_text(db, _user_id(user), text).as_dict()


def list_conflicts(db: Session, user, *, topic: str | None = None, limit: int = 10, offset: int = 0) -> dict:
    """List disagreements between sources, as two-sided camps.

    Use to answer "where do my sources contradict each other". Returns each
    contested cluster with both camps and the representative claim from each, with
    the quotes and dates that make the disagreement checkable.
    """
    owner = _user_id(user)
    rows = (
        db.execute(
            select(ClaimRelation)
            .where(
                ClaimRelation.user_id == owner,
                ClaimRelation.relation == "contradicts",
                ClaimRelation.resolved_at.is_(None),
            )
            .order_by(ClaimRelation.confidence.desc(), ClaimRelation.id.asc())
        )
        .scalars()
        .all()
    )
    if not rows:
        return {"conflicts": [], "count": 0}

    out: list[dict] = []
    seen: set[tuple[int, int]] = set()
    skipped = 0
    max_items = max(1, min(limit, 50))
    for relation in rows:
        key = (relation.a_claim_id, relation.b_claim_id)
        if key in seen:
            continue
        seen.add(key)
        a = db.get(Claim, relation.a_claim_id)
        b = db.get(Claim, relation.b_claim_id)
        if a is None or b is None:
            continue
        cluster_id = a.cluster_id or b.cluster_id
        cluster = _cluster(db, cluster_id) if cluster_id else None
        label = cluster.label if cluster is not None else ""
        if topic and topic.lower() not in (label or "").lower():
            continue
        if skipped < offset:
            skipped += 1
            continue
        camps = _camps(db, cluster_id) if cluster_id else None
        out.append(
            {
                "cluster_id": cluster_id,
                "label": label,
                "confidence": relation.confidence,
                "rationale": relation.rationale,
                "a": _claim_out(db, a),
                "b": _claim_out(db, b),
                "camps": camps,
            }
        )
        if len(out) >= max_items:
            break
    return {"conflicts": out, "count": len(out)}


def _cluster(db: Session, cluster_id: int):
    from app.models.claim import ClaimCluster

    return db.get(ClaimCluster, cluster_id)


def _camps(db: Session, cluster_id: int) -> dict | None:
    claims, relations = cluster_camp_inputs(db, cluster_id)
    if not claims:
        return None
    camps = compute_camps(claims, relations)
    if len(camps.camps) < 2:
        return None
    return {
        "balanced": camps.balanced,
        "camps": [
            {
                "label": camp.label,
                "claims": [
                    _claim_out(db, db.get(Claim, cid)) for cid in camp.claim_ids if db.get(Claim, cid)
                ],
            }
            for camp in camps.camps
        ],
    }


def _claim_out(db: Session, claim: Claim | None) -> dict:
    if claim is None:
        return {}
    document = db.get(Document, claim.document_id)
    source = db.get(Source, document.source_id) if document else None
    return {
        "claim_id": claim.id,
        "statement": claim.statement,
        "quote": claim.quote,
        "char_start": claim.char_start,
        "char_end": claim.char_end,
        "status": claim.status,
        "document_id": str(claim.document_id),
        "title": document.title if document else "",
        "url": document.url if document else None,
        "source": source.name if source else "",
        "date": _date(claim.asserted_at),
    }


def what_changed(
    db: Session,
    user,
    *,
    since: str,
    until: str | None = None,
    topic: str | None = None,
) -> dict:
    """What changed over a period, as a readable digest.

    Use for "what did I learn about X recently". `since` is an ISO date;
    `until` defaults to now. Windows longer than 90 days are clamped, because a
    digest over years is a backlog rather than an answer. `topic` filters by cluster
    label.
    """
    from app.temporal.belief_diff import compute_belief_diff

    start = _parse_since(since)
    if start is None:
        raise DomainValidationError("`since` is required", code="missing_since")
    end = _parse_since(until) or clock.utcnow()
    if end <= start:
        raise DomainValidationError("`until` must be after `since`", code="bad_period")
    if (end - start).days > MAX_DIFF_DAYS:
        start = end - timedelta(days=MAX_DIFF_DAYS)

    scope_kind, scope_id = "all", None
    if topic:
        from app.models.claim import ClaimCluster

        match = (
            db.execute(
                select(ClaimCluster).where(
                    ClaimCluster.user_id == _user_id(user), ClaimCluster.label.ilike(f"%{topic}%")
                )
            )
            .scalars()
            .first()
        )
        if match is None:
            return {"period": {"start": start.isoformat(), "end": end.isoformat()}, "digest": "", "stats": {}}
        scope_kind, scope_id = "cluster", match.id

    result = compute_belief_diff(
        db, _user_id(user), start=start, end=end, scope_kind=scope_kind, scope_id=scope_id
    )
    return {
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "topic": topic,
        "digest": result.digest_md,
        "stats": result.stats,
    }


def read_wiki(db: Session, user, *, slug: str) -> dict:
    """Read a compiled wiki page.

    Use for a synthesised overview of a topic: every sentence on the page is a source
    claim with a footnote, and the page also lists the claims it was built from.
    """
    owner = _user_id(user)
    page = db.execute(
        select(WikiPage).where(WikiPage.user_id == owner, WikiPage.slug == slug)
    ).scalar_one_or_none()
    if page is None:
        raise NotFoundError(f"no wiki page with slug {slug!r}")
    from app.models.wiki import WikiPageClaim

    claim_ids = list(
        db.execute(select(WikiPageClaim.claim_id).where(WikiPageClaim.page_id == page.id)).scalars()
    )
    return {
        "slug": page.slug,
        "title": page.title,
        "revision": page.revision,
        "compiled_at": _date(page.compiled_at),
        "compile_method": page.compile_method,
        "body_md": page.body_md,
        "claim_ids": claim_ids,
    }


def search_wiki(db: Session, user, *, query: str, limit: int = 10) -> dict:
    """Find wiki pages by title or body text.

    Use before `read_wiki` when you know the subject but not the page. Matching is
    substring-based, so partial words work; results carry the slug to read next.
    """
    owner = _user_id(user)
    needle = (query or "").strip().lower()
    if not needle:
        return {"query": query, "pages": [], "count": 0}

    pages = list(
        db.execute(select(WikiPage).where(WikiPage.user_id == owner).order_by(WikiPage.slug)).scalars()
    )
    hits = []
    for page in pages:
        title = (page.title or "").lower()
        body = (page.body_md or "").lower()
        if needle in title or needle in body:
            hits.append(
                {
                    "slug": page.slug,
                    "title": page.title,
                    "status": page.status,
                    "revision": page.revision,
                    "excerpt": (page.body_md or "")[:240],
                }
            )
        if len(hits) >= max(1, min(limit, 50)):
            break
    return {"query": query, "pages": hits, "count": len(hits)}


def capture(db: Session, user, *, url_or_text: str, title: str | None = None) -> dict:
    """Save a URL or a piece of text into the user's library.

    Use to add a source the user mentions. A value starting with http(s) creates a
    webpage source and queues an ingest; anything else is stored as a note. Needs a
    token with the `write` scope. Returns the new source and the queued job.
    """
    from app.ingestion.sources import create_source
    from app.jobs import queue as q

    raw = (url_or_text or "").strip()
    if not raw:
        raise DomainValidationError("nothing to capture", code="empty_capture")

    owner = _user_id(user)
    # Checked here as well as in the MCP wrapper: the tool is also reachable over the
    # REST bridge, and a scope promised by one transport must hold on the other.
    if hasattr(user, "has") and not user.has("write"):
        raise PermissionDeniedError("capture needs the 'write' scope", code="insufficient_scope")
    is_url = raw.startswith(("http://", "https://"))
    if is_url:
        from app.ingestion.netguard import SSRFError, validate_url

        try:
            validate_url(raw)
        except SSRFError as exc:
            raise DomainValidationError(exc.message, code=exc.code) from exc
        source = create_source(
            db,
            owner,
            source_type="webpage",
            name=title or canonicalize_url(raw),
            url=raw,
        )
        job = q.enqueue(
            db,
            "ingest_source",
            {"source_id": str(source.id)},
            user_id=owner,
            dedupe_key=f"ingest:{source.id}",
        )
        db.commit()
        return {
            "source_id": str(source.id),
            "source_type": source.source_type,
            "name": source.name,
            "url": source.url,
            "job_id": job.id,
        }
    else:
        source = create_source(
            db,
            owner,
            source_type="note",
            name=title or raw[:80],
            url=None,
        )
        db.flush()
        from app.ingestion.types import ExtractedDocument
        from app.ingestion.versions import upsert_document_version

        doc = ExtractedDocument(title=title or source.name, text=raw, url=None)
        res = upsert_document_version(db, source=source, extracted=doc, origin="upload")
        db.commit()
        job_id = None
        if res.version:
            job = q.enqueue(
                db,
                "index_version",
                {"version_id": res.version.id},
                user_id=owner,
                dedupe_key=f"index-note:{source.id}",
            )
            db.commit()
            job_id = job.id
        return {
            "source_id": str(source.id),
            "source_type": source.source_type,
            "name": source.name,
            "url": source.url,
            "job_id": job_id,
        }


def export_brief(db: Session, user, *, kind: str, id: str) -> dict:
    """Export an answer or a topic as an evidence brief in Markdown.

    `kind` is "answer" (pass the answer's id) or "topic" (pass the topic's numeric id).
    The brief lists every sentence or claim with a verdict badge, numbered footnotes
    that quote the exact source text, and the disputes it rests on.
    """
    from app.briefs import build_answer_brief, build_topic_brief

    owner = _user_id(user)
    if kind == "answer":
        try:
            answer_id = uuid.UUID(id)
        except ValueError as exc:
            raise DomainValidationError("`id` must be an answer UUID", code="bad_id") from exc
        markdown = build_answer_brief(db, owner, answer_id)
    elif kind == "topic":
        try:
            topic_id = int(id)
        except ValueError as exc:
            raise DomainValidationError("`id` must be a topic number", code="bad_id") from exc
        markdown = build_topic_brief(db, owner, topic_id)
    else:
        raise DomainValidationError("`kind` must be 'answer' or 'topic'", code="bad_kind")
    return {"kind": kind, "id": id, "format": "md", "markdown": markdown}


TOOLS = {
    "search_sources": search_sources,
    "get_passage": get_passage,
    "ask": ask,
    "verify": verify,
    "list_conflicts": list_conflicts,
    "what_changed": what_changed,
    "read_wiki": read_wiki,
    "search_wiki": search_wiki,
    "capture": capture,
    "export_brief": export_brief,
}

WRITE_TOOLS = {"capture"}


__all__ = [
    "MAX_DIFF_DAYS",
    "TOOLS",
    "WRITE_TOOLS",
    "ask",
    "capture",
    "export_brief",
    "get_passage",
    "list_conflicts",
    "read_wiki",
    "search_sources",
    "search_wiki",
    "verify",
    "what_changed",
]
