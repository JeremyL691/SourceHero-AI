"""Consistency checks over the wiki.

Lint is advisory except where it counts: `uncited_sentence` should be structurally
impossible once the validator runs, so seeing one means the validator was bypassed.
Findings that no longer apply are closed rather than deleted, so the history of what
was wrong is inspectable.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import time as clock
from app.core.logging import get_logger
from app.models.claim import Claim, ClaimCluster
from app.models.wiki import LintFinding, WikiLink, WikiPage, WikiPageClaim

log = get_logger(__name__)

MIN_CLAIMS_FOR_CLUSTER = 3

SEVERITIES = ("error", "warn", "info")


def lint_wiki(db: Session, user_id) -> dict:
    """Run every check and refresh the findings list."""
    pages = list(db.execute(select(WikiPage).where(WikiPage.user_id == user_id)).scalars())
    claims = {c.id: c for c in db.execute(select(Claim).where(Claim.user_id == user_id)).scalars()}

    findings: list[dict] = []
    for page in pages:
        findings.extend(_check_page(db, page, claims))
    findings.extend(_check_clusters(db, user_id, pages))
    findings.extend(_check_links(db, user_id, pages))
    findings.extend(_check_orphans(db, user_id, pages))

    _persist(db, user_id, findings)
    counts = {severity: sum(1 for f in findings if f["severity"] == severity) for severity in SEVERITIES}
    return {
        "pages": len(pages),
        "findings": len(findings),
        "by_severity": counts,
        "items": findings,
    }


def _check_page(db: Session, page: WikiPage, claims: dict[int, Claim]) -> list[dict]:
    from app.wiki.compiler import _camp_map
    from app.wiki.validator import covers_dispute

    body = page.body or {}
    out: list[dict] = []

    sentences = _sentences(body)
    for sentence in sentences:
        if not sentence.get("claim_ids"):
            out.append(
                {
                    "page_id": page.id,
                    "kind": "uncited_sentence",
                    "severity": "error",
                    "detail": {
                        "text": sentence.get("text", "")[:200],
                        "target": sentence.get("text", "")[:50],
                    },
                }
            )

    member_ids = {c.get("id") for c in (page.compiled_input or [])}
    for sentence in sentences:
        stray = [cid for cid in sentence.get("claim_ids", []) if cid not in member_ids]
        if stray:
            out.append(
                {
                    "page_id": page.id,
                    "kind": "unknown_claim_reference",
                    "severity": "error",
                    "detail": {"claim_ids": stray, "target": ",".join(str(x) for x in sorted(stray))},
                }
            )

    # A clean page that cites a since-superseded claim in a non-history section should have been rebuilt.
    if page.status == "clean":
        non_history_sentences = list(body.get("summary") or [])
        for sec in body.get("sections") or []:
            if sec.get("id") != "history" and sec.get("kind") != "history":
                non_history_sentences.extend(sec.get("sentences") or [])
        non_history_cids = {cid for s in non_history_sentences for cid in s.get("claim_ids", [])}
        for cid in non_history_cids:
            claim = claims.get(cid)
            if claim is not None and claim.status in ("superseded", "retracted"):
                out.append(
                    {
                        "page_id": page.id,
                        "kind": "stale_claim_cited",
                        "severity": "warn",
                        "detail": {"claim_id": cid, "status": claim.status, "target": f"claim:{cid}"},
                    }
                )
                break

    for d in body.get("dropped") or []:
        text = d.get("text", "")
        out.append(
            {
                "page_id": page.id,
                "kind": "dropped_sentence",
                "severity": "info",
                "detail": {
                    "text": text[:200],
                    "where": d.get("where"),
                    "reason": d.get("reason"),
                    "target": f"dropped:{text[:50]}",
                },
            }
        )

    page_claims = [claims[cid] for cid in member_ids if cid in claims]
    if page_claims:
        camps = _camp_map(db, page_claims)
        # `_as_schema` already returns a WikiBody; unpacking it into the constructor
        # again raises TypeError.
        if not covers_dispute(_as_schema(body), claims, camps):
            out.append(
                {
                    "page_id": page.id,
                    "kind": "dispute_not_covered",
                    "severity": "warn",
                    "detail": {"camps": sorted(set(camps.values())), "target": "dispute"},
                }
            )
    return out


def _sentences(body: dict) -> list[dict]:
    out = list(body.get("summary") or [])
    for section in body.get("sections") or []:
        out.extend(section.get("sentences") or [])
    return out


def _as_schema(body: dict):
    from app.llm.schemas import WikiBody

    return WikiBody(
        title=body.get("title", ""),
        summary=body.get("summary") or [],
        sections=body.get("sections") or [],
        see_also=body.get("see_also") or [],
    )


def _check_clusters(db: Session, user_id, pages: list[WikiPage]) -> list[dict]:
    """Substantial clusters that belong to no topic, hence to no page."""
    assigned = {
        claim_id
        for (claim_id,) in db.execute(
            select(WikiPageClaim.claim_id)
            .join(WikiPage, WikiPage.id == WikiPageClaim.page_id)
            .where(WikiPage.user_id == user_id)
        ).all()
    }
    rows = (
        db.execute(
            select(ClaimCluster).where(
                ClaimCluster.user_id == user_id, ClaimCluster.size >= MIN_CLAIMS_FOR_CLUSTER
            )
        )
        .scalars()
        .all()
    )

    out = []
    for cluster in rows:
        members = list(
            db.execute(
                select(Claim.id).where(Claim.cluster_id == cluster.id, Claim.status == "active")
            ).scalars()
        )
        if len(members) >= MIN_CLAIMS_FOR_CLUSTER and not (set(members) & assigned):
            out.append(
                {
                    "page_id": None,
                    "kind": "uncovered_cluster",
                    "severity": "warn",
                    "detail": {
                        "cluster_id": cluster.id,
                        "label": cluster.label,
                        "size": len(members),
                        "target": f"cluster:{cluster.id}",
                    },
                }
            )
    return out


def _check_links(db: Session, user_id, pages: list[WikiPage]) -> list[dict]:
    known = {p.id for p in pages}
    links = (
        db.execute(
            select(WikiLink)
            .join(WikiPage, WikiPage.id == WikiLink.from_page_id)
            .where(WikiPage.user_id == user_id)
        )
        .scalars()
        .all()
    )
    out = []
    for link in links:
        if link.from_page_id not in known or link.to_page_id not in known:
            out.append(
                {
                    "page_id": link.from_page_id if link.from_page_id in known else None,
                    "kind": "broken_link",
                    "severity": "error",
                    "detail": {
                        "from": link.from_page_id,
                        "to": link.to_page_id,
                        "target": f"{link.from_page_id}->{link.to_page_id}",
                    },
                }
            )
    return out


def _check_orphans(db: Session, user_id, pages: list[WikiPage]) -> list[dict]:
    linked_to_ids = {
        row[0]
        for row in db.execute(
            select(WikiLink.to_page_id)
            .join(WikiPage, WikiPage.id == WikiLink.from_page_id)
            .where(WikiPage.user_id == user_id)
        ).all()
    }
    out = []
    for page in pages:
        if page.slug in ("index", "home", "main"):
            continue
        if page.id not in linked_to_ids:
            out.append(
                {
                    "page_id": page.id,
                    "kind": "orphan_page",
                    "severity": "info",
                    "detail": {"slug": page.slug, "target": page.slug},
                }
            )
    return out


def _finding_target(finding: dict) -> str:
    detail = finding.get("detail") or {}
    if "target" in detail:
        return str(detail["target"])
    if "cluster_id" in detail:
        return f"cluster:{detail['cluster_id']}"
    if "to" in detail:
        return f"link:{detail.get('from')}->{detail.get('to')}"
    if "claim_id" in detail:
        return f"claim:{detail['claim_id']}"
    if "text" in detail:
        return f"text:{detail['text'][:50]}"
    if "slug" in detail:
        return f"page:{detail['slug']}"
    return ""


def _persist(db: Session, user_id, findings: list[dict]) -> None:
    """Store new findings and close any that no longer reproduce."""
    now = clock.utcnow()
    seen: set[tuple] = set()

    for finding in findings:
        target = _finding_target(finding)
        if "target" not in finding.get("detail", {}):
            finding.setdefault("detail", {})["target"] = target
        key = (finding["kind"], finding["page_id"], target)
        seen.add(key)
        open_findings = (
            db.execute(
                select(LintFinding).where(
                    LintFinding.user_id == user_id,
                    LintFinding.kind == finding["kind"],
                    LintFinding.page_id.is_(finding["page_id"])
                    if finding["page_id"] is None
                    else LintFinding.page_id == finding["page_id"],
                    LintFinding.resolved_at.is_(None),
                )
            )
            .scalars()
            .all()
        )
        existing = next(
            (
                f
                for f in open_findings
                if ((f.detail or {}).get("target") or _finding_target({"detail": f.detail or {}})) == target
            ),
            None,
        )
        if existing is None:
            db.add(
                LintFinding(
                    user_id=user_id,
                    page_id=finding["page_id"],
                    kind=finding["kind"],
                    severity=finding["severity"],
                    detail=finding["detail"],
                )
            )
        else:
            existing.detail = finding["detail"]
            existing.severity = finding["severity"]

    for open_finding in db.execute(
        select(LintFinding).where(LintFinding.user_id == user_id, LintFinding.resolved_at.is_(None))
    ).scalars():
        open_target = (open_finding.detail or {}).get("target") or _finding_target(
            {"detail": open_finding.detail or {}}
        )
        key = (open_finding.kind, open_finding.page_id, open_target)
        if key not in seen:
            open_finding.resolved_at = now
    db.flush()


__all__ = ["MIN_CLAIMS_FOR_CLUSTER", "SEVERITIES", "lint_wiki"]
