"""Page compilation: claims in, a cited page out.

`input_hash` is the build system's fingerprint of a page's inputs -- the prompt
version, the model and the exact set of claims. Change any of them and only the pages
whose fingerprint moved are recompiled.

The validator runs on *every* page, whatever produced it. A page that cites a claim it
was not given, or states a number no cited claim contains, is not shipped: the sentence
is dropped and recorded as a lint finding. That is the property that makes the wiki
checkable rather than merely plausible.
"""

from __future__ import annotations

import hashlib
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import time as clock
from app.core.logging import get_logger
from app.llm.schemas import WikiBody, WikiSection, WikiSentence
from app.models.claim import Claim, ClaimCluster, ClaimRelation
from app.models.wiki import WikiPage, WikiPageClaim, WikiPageRevision

log = get_logger(__name__)

MAX_LLM_CLAIMS = 120


class NoLiveProvider(Exception):
    """No model is configured, so the page is compiled heuristically."""

    code = "no_provider"


def page_inputs(db: Session, page: WikiPage) -> list[dict]:
    """The claims a page is compiled from, in a stable order.

    Superseded claims are included on purpose: a page must be able to say *what
    changed*, which means it needs the old claim too.
    """
    cluster_ids = (
        db.execute(select(ClaimCluster.id).where(ClaimCluster.topic_id == page.topic_id)).scalars().all()
    )
    if not cluster_ids:
        return []
    claims = (
        db.execute(
            select(Claim)
            .where(Claim.cluster_id.in_(cluster_ids), Claim.status.in_(("active", "superseded")))
            .order_by(Claim.id)
        )
        .scalars()
        .all()
    )

    relations = _relations_for(db, [c.id for c in claims])
    return [
        {
            "id": c.id,
            "claim_hash": c.claim_hash,
            "status": c.status,
            "cluster_id": c.cluster_id,
            "relations": relations.get(c.id, []),
        }
        for c in claims
    ]


def _relations_for(db: Session, claim_ids: list[int]) -> dict[int, list[tuple]]:
    """For each claim, the (other id, relation, direction, user_label, resolved_at) tuples touching it."""
    if not claim_ids:
        return {}
    rows = (
        db.execute(
            select(ClaimRelation).where(
                (ClaimRelation.a_claim_id.in_(claim_ids)) | (ClaimRelation.b_claim_id.in_(claim_ids))
            )
        )
        .scalars()
        .all()
    )
    out: dict[int, list[tuple]] = {}
    for relation in rows:
        resolved_str = relation.resolved_at.isoformat() if relation.resolved_at else None
        item_forward = (
            relation.b_claim_id,
            relation.relation,
            relation.direction,
            relation.user_label,
            resolved_str,
        )
        item_reverse = (
            relation.a_claim_id,
            relation.relation,
            relation.direction,
            relation.user_label,
            resolved_str,
        )
        if relation.a_claim_id in claim_ids:
            out.setdefault(relation.a_claim_id, []).append(item_forward)
        if relation.b_claim_id in claim_ids:
            out.setdefault(relation.b_claim_id, []).append(item_reverse)
    for value in out.values():
        value.sort()
    return out


def input_hash(
    inputs: list[dict],
    *,
    prompt_version: str = "v1",
    model: str = "heuristic",
    provider: str = "heuristic",
    prompt: str = "wiki_compile",
) -> str:
    """Order-independent fingerprint of everything that determines the output.

    `sort_keys` plus a sorted input list means re-running the planner cannot produce a
    different hash for the same content -- otherwise every page would look dirty on
    every tick and recompile forever.
    """
    payload = {
        "pv": prompt_version,
        "prompt": prompt,
        "provider": provider,
        "model": model,
        "inputs": sorted(inputs, key=lambda item: item.get("id") or 0),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()


# --- compilation ---------------------------------------------------------


def compile_page(db: Session, page: WikiPage, *, force: bool = False) -> dict:
    """Compile one page. Returns a stats dict; the page carries the result."""
    from app.llm.client import get_llm
    from app.llm.prompts import get_prompt
    from app.wiki.render import render_markdown
    from app.wiki.validator import validate_body

    client = get_llm(db=db)
    if client.effective.provider != "heuristic" and client.mode == "live":
        provider = client.effective.provider
        model = client.effective.model_smart or "smart"
        prompt_name = "wiki_compile"
        prompt_version = get_prompt("wiki_compile").version
    else:
        provider = "heuristic"
        model = "heuristic"
        prompt_name = "wiki_compile"
        prompt_version = "v1"

    inputs = page_inputs(db, page)
    digest = input_hash(
        inputs,
        prompt_version=prompt_version,
        model=model,
        provider=provider,
        prompt=prompt_name,
    )
    if not force and digest == page.input_hash and page.status == "clean":
        return {"skipped": "unchanged", "revision": page.revision}

    page.status = "compiling"
    db.flush()

    claims = _claims_by_id(db, [i["id"] for i in inputs])
    if not claims:
        page.status = "clean"
        page.input_hash = digest
        page.compiled_input = inputs
        page.revision += 1
        db.flush()
        return {"claims": 0, "revision": page.revision}

    ordered = list(claims.values())
    try:
        body, method = _compose(db, page, ordered, inputs)
    except Exception as exc:  # noqa: BLE001 - a failed compile marks the page, not the request
        page.status = "failed"
        page.error = f"{type(exc).__name__}: {exc}"[:2000]
        db.flush()
        log.warning("wiki: page %s failed to compile: %s", page.slug, exc)
        return {"error": page.error}

    # The validator looks claims up by id, and must receive the whole page's set.
    body, dropped = validate_body(body, claims, allow_paraphrase=(method != "heuristic"))

    body_dict = body.model_dump()
    if dropped:
        body_dict["dropped"] = dropped
    page.body = body_dict
    page.body_md = render_markdown(body)
    page.input_hash = digest
    page.compiled_input = inputs
    page.compile_method = method
    page.compiled_at = clock.utcnow()
    page.status = "clean"
    page.error = None
    page.revision += 1
    db.flush()

    _record_revision(db, page, body)
    _record_provenance(db, page, [i["id"] for i in inputs])
    return {
        "claims": len(inputs),
        "sentences": _sentence_count(body),
        "dropped": dropped,
        "revision": page.revision,
        "method": method,
    }


def _compose(db: Session, page: WikiPage, claims: list[Claim], inputs: list[dict]):
    """Heuristic full compile, or the model when one is configured."""
    try:
        return _llm_compile(db, page, claims, inputs)
    except Exception as exc:  # noqa: BLE001 - the heuristic path is always available
        log.info("wiki: %s compiling heuristically (%s)", page.slug, getattr(exc, "code", exc))
        return heuristic_body(db, page, claims), "heuristic"


def build_wiki_compile_context(
    topic: str,
    claims: list[dict],
    related_slugs: list[str] | None = None,
    language: str = "the language of the claims",
) -> dict:
    return {
        "topic": topic,
        "language": language,
        "claims": claims,
        "related_slugs": related_slugs or [],
    }


def build_wiki_patch_context(
    topic: str,
    page_json: str,
    added: list[dict] | None = None,
    removed_ids: list[int] | None = None,
    changed: list[dict] | None = None,
    claims: list[dict] | None = None,
) -> dict:
    return {
        "topic": topic,
        "page_json": page_json,
        "added": added or [],
        "removed_ids": removed_ids or [],
        "changed": changed or [],
        "claims": claims or [],
    }


def _llm_compile(db: Session, page: WikiPage, claims: list[Claim], inputs: list[dict]):
    from app.llm.client import get_llm
    from app.llm.prompts import render
    from app.llm.types import LLMRequest

    client = get_llm(db=db, user_id=page.user_id)
    if client.mode != "live":
        raise NoLiveProvider("no live provider configured")

    camps = _camp_map(db, claims)
    payload = [
        {
            "id": c.id,
            "source": _source_name(db, c),
            "date": c.asserted_at.date().isoformat(),
            "status": c.status,
            "camp": camps.get(c.id),
            "statement": c.statement,
        }
        for c in claims[:MAX_LLM_CLAIMS]
    ]
    ctx = build_wiki_compile_context(
        topic=page.title,
        claims=payload,
        related_slugs=[],
        language="the language of the claims",
    )
    messages, version = render(
        "wiki_compile",
        schema=WikiBody,
        **ctx,
    )
    result = client.complete(
        LLMRequest(
            task="wiki_compile",
            messages=messages,
            tier="smart",
            schema=WikiBody,
            prompt_version=version,
            max_tokens=6000,  # a reasoning model spends part of it before the JSON (K3.4 evidence)
            cache=False,
        )
    )
    assert result.parsed is not None
    return result.parsed, f"llm:{result.model}"


def heuristic_body(db: Session, page: WikiPage, claims: list[Claim]) -> WikiBody:
    """The offline compiler.

    Extractive by construction: every sentence is a source claim's own statement with
    its source and date attached, so there is nothing here a validator could catch
    that is not already true of the source.
    """
    camps = _camp_map(db, claims)
    superseded = {c.id: db.get(Claim, c.superseded_by) for c in claims if c.superseded_by}
    active_claims = [c for c in claims if c.status == "active"]

    summary: list[WikiSentence] = []
    for claim in active_claims[:2] or claims[:2]:
        summary.append(
            WikiSentence(
                text=_attributed(db, claim, camps.get(claim.id)),
                claim_ids=[claim.id],
            )
        )

    sections: list[WikiSection] = []

    if active_claims:
        sentences = [WikiSentence(text=_attributed(db, c), claim_ids=[c.id]) for c in active_claims[:6]]
        sections.append(WikiSection(id="overview", heading="Overview", kind="overview", sentences=sentences))

    # `disputed_ids` holds claim *ids*, so each side is resolved back to its Claim
    # before rendering; handing the id straight to `_attributed` reads
    # `.document_id` off an int.
    disputed_ids = [c.id for c in claims if camps.get(c.id) and len(set(camps.values())) > 1]
    if disputed_ids:
        by_id = {c.id: c for c in claims}
        sentences = []
        for camp in sorted({camps[cid] for cid in disputed_ids}):
            for claim_id in [cid for cid in disputed_ids if camps[cid] == camp]:
                claim = by_id.get(claim_id)
                if claim is None:
                    continue
                sentences.append(WikiSentence(text=_attributed(db, claim, camp), claim_ids=[claim.id]))
        sections.append(
            WikiSection(
                id="disputed",
                heading="Disputed",
                kind="disputed",
                sentences=sentences,
            )
        )

    if superseded:
        sentences = []
        for claim in claims:
            successor = superseded.get(claim.id)
            if successor is None:
                continue
            sentences.append(
                WikiSentence(
                    text=(
                        f"Earlier, {_source_name(db, claim)} ({claim.asserted_at.date().isoformat()}) "
                        f"stated that {claim.statement} This was later updated: "
                        f"{successor.statement}"
                    ),
                    claim_ids=[claim.id, successor.id],
                )
            )
        if sentences:
            sections.append(WikiSection(id="history", heading="History", kind="history", sentences=sentences))

    return WikiBody(title=page.title, summary=summary, sections=sections, see_also=[])


def _attributed(db: Session, claim: Claim, camp: str | None = None) -> str:
    """`{source} ({date}) reports that {statement}` -- the source says it, not us."""
    prefix = f"{_source_name(db, claim)} ({claim.asserted_at.date().isoformat()}) reports that "
    return prefix + claim.statement


def _source_name(db: Session, claim: Claim) -> str:
    from app.models.document import Document
    from app.models.source import Source

    document = db.get(Document, claim.document_id)
    if document is None:
        return "an unnamed source"
    source = db.get(Source, document.source_id)
    return (source.name if source and source.name else document.title) or "an unnamed source"


def _camp_map(db: Session, claims: list[Claim]) -> dict[int, str]:
    """Label each claim with the side of the disagreement it sits on."""
    from app.claims.camps import compute_camps

    out: dict[int, str] = {}
    by_cluster: dict[int, list[Claim]] = {}
    for claim in claims:
        if claim.cluster_id is not None:
            by_cluster.setdefault(claim.cluster_id, []).append(claim)

    for _cluster_id, members in by_cluster.items():
        ids = [m.id for m in members]
        relations = (
            db.execute(
                select(ClaimRelation).where(
                    ClaimRelation.a_claim_id.in_(ids), ClaimRelation.b_claim_id.in_(ids)
                )
            )
            .scalars()
            .all()
        )
        camps = compute_camps(members, list(relations))
        if len(camps.camps) < 2:
            continue
        for camp in camps.camps:
            for claim_id in camp.claim_ids:
                out[claim_id] = camp.label
    return out


def _claims_by_id(db: Session, claim_ids: list[int]) -> dict[int, Claim]:
    """Claims keyed by id.

    A dict, because both consumers need lookup by id: the validator answers "is this
    sentence's cited claim on this page?" and the camp map answers "which side is
    this claim on?". Passing a list makes `cid not in claims` compare a claim id
    against claim *objects*, so every sentence looks like it cites something unknown.
    """
    if not claim_ids:
        return {}
    rows = db.execute(select(Claim).where(Claim.id.in_(claim_ids)).order_by(Claim.id)).scalars()
    return {c.id: c for c in rows}


def _record_revision(db: Session, page: WikiPage, body: WikiBody) -> None:
    """Store this state as a revision, so a reader can diff what changed."""
    db.add(
        WikiPageRevision(
            page_id=page.id,
            revision=page.revision,
            body=body.model_dump(),
            body_md=page.body_md,
            input_hash=page.input_hash,
            change_summary=f"compiled from {len(page.compiled_input or [])} claims",
        )
    )
    db.flush()


def _record_provenance(db: Session, page: WikiPage, claim_ids: list[int]) -> None:
    """Replace the page's claim set wholesale.

    Incremental union would keep citing claims the page no longer mentions.
    """
    for existing in db.execute(select(WikiPageClaim).where(WikiPageClaim.page_id == page.id)).scalars():
        db.delete(existing)
    db.flush()
    for claim_id in claim_ids:
        db.add(WikiPageClaim(page_id=page.id, claim_id=claim_id))
    db.flush()


def _sentence_count(body: WikiBody) -> int:
    return len(body.summary) + sum(len(s.sentences) for s in body.sections)


def diff_revisions(db: Session, page: WikiPage, from_revision: int, to_revision: int) -> str:
    """Unified diff between two stored revisions of a page."""
    import difflib

    def body_of(revision: int) -> str:
        row = db.execute(
            select(WikiPageRevision).where(
                WikiPageRevision.page_id == page.id, WikiPageRevision.revision == revision
            )
        ).scalar_one_or_none()
        return row.body_md if row else ""

    return "\n".join(
        difflib.unified_diff(
            body_of(from_revision).splitlines(),
            body_of(to_revision).splitlines(),
            fromfile=f"r{from_revision}",
            tofile=f"r{to_revision}",
            lineterm="",
            n=2,
        )
    )


__all__ = [
    "MAX_LLM_CLAIMS",
    "compile_page",
    "diff_revisions",
    "heuristic_body",
    "input_hash",
    "page_inputs",
]
