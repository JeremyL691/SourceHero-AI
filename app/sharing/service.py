"""Create, list, revoke and resolve share links; build the public snapshot."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import time as clock
from app.core.errors import NotFoundError
from app.llm.schemas import WikiBody
from app.models.answer import Answer
from app.models.claim import Claim
from app.models.document import Document
from app.models.sharing import ShareLink
from app.models.wiki import WikiPage
from app.schemas.sharing import (
    PublicAnswerShare,
    PublicCitation,
    PublicFootnote,
    PublicSection,
    PublicSentence,
    PublicShare,
    PublicWikiShare,
)
from app.wiki.render import extract_footnotes


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _owns_target(db: Session, user_id: uuid.UUID, kind: str, target_id: str) -> bool:
    if kind == "wiki_page":
        stmt = select(WikiPage.id).where(WikiPage.user_id == user_id, WikiPage.slug == target_id)
    else:
        try:
            answer_id = uuid.UUID(target_id)
        except ValueError:
            return False
        stmt = select(Answer.id).where(Answer.user_id == user_id, Answer.id == answer_id)
    return db.execute(stmt.limit(1)).first() is not None


def create_share(
    db: Session, user_id: uuid.UUID, kind: str, target_id: str, expires_in_days: int | None
) -> tuple[ShareLink, str]:
    if not _owns_target(db, user_id, kind, target_id):
        raise NotFoundError("target not found")
    token = secrets.token_urlsafe(32)
    expires_at = clock.utcnow() + timedelta(days=expires_in_days) if expires_in_days else None
    row = ShareLink(
        user_id=user_id,
        kind=kind,
        target_id=target_id,
        token_hash=hash_token(token),
        expires_at=expires_at,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row, token


def list_shares(db: Session, user_id: uuid.UUID) -> list[ShareLink]:
    return list(
        db.execute(
            select(ShareLink).where(ShareLink.user_id == user_id).order_by(ShareLink.id.desc())
        ).scalars()
    )


def revoke_share(db: Session, user_id: uuid.UUID, share_id: int) -> None:
    row = db.execute(
        select(ShareLink).where(ShareLink.id == share_id, ShareLink.user_id == user_id)
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError("share not found")
    if row.revoked_at is None:
        row.revoked_at = clock.utcnow()
        db.commit()


def find_live_share(db: Session, token: str) -> ShareLink | None:
    """A share that is neither revoked nor expired, or None."""
    row = db.execute(select(ShareLink).where(ShareLink.token_hash == hash_token(token))).scalar_one_or_none()
    if row is None or row.revoked_at is not None:
        return None
    if row.expires_at is not None and row.expires_at <= clock.utcnow():
        return None
    return row


def public_snapshot(db: Session, share: ShareLink) -> PublicShare | None:
    """A read-only copy of the target with no owner, email or internal ids except the slug."""
    if share.kind == "wiki_page":
        return _wiki_snapshot(db, share)
    return _answer_snapshot(db, share)


def _wiki_snapshot(db: Session, share: ShareLink) -> PublicWikiShare | None:
    page = db.execute(
        select(WikiPage).where(WikiPage.user_id == share.user_id, WikiPage.slug == share.target_id)
    ).scalar_one_or_none()
    if page is None:
        return None
    body = WikiBody.model_validate(page.body) if page.body else WikiBody(title=page.title)
    pairs = extract_footnotes(body)
    number_by_claim = {cid: n for n, cid in pairs}
    footnotes: list[PublicFootnote] = []
    for n, claim_id in pairs:
        claim = db.get(Claim, claim_id)
        doc = db.get(Document, claim.document_id) if claim and claim.document_id else None
        footnotes.append(
            PublicFootnote(
                n=n,
                quote=(claim.quote or claim.statement) if claim else "",
                document_title=doc.title if doc and doc.title else "",
                url=doc.url if doc else None,
            )
        )

    def sentence(s) -> PublicSentence:
        cites = [number_by_claim[c] for c in s.claim_ids if c in number_by_claim]
        return PublicSentence(text=s.text, citations=cites)

    return PublicWikiShare(
        slug=page.slug,
        title=page.title,
        summary=[sentence(s) for s in body.summary],
        sections=[
            PublicSection(heading=sec.heading, sentences=[sentence(s) for s in sec.sentences])
            for sec in body.sections
        ],
        footnotes=footnotes,
    )


def _answer_snapshot(db: Session, share: ShareLink) -> PublicAnswerShare | None:
    try:
        answer_id = uuid.UUID(share.target_id)
    except ValueError:
        return None
    row = db.execute(
        select(Answer).where(Answer.id == answer_id, Answer.user_id == share.user_id)
    ).scalar_one_or_none()
    if row is None:
        return None
    sentences = [
        PublicSentence(
            text=str(s.get("text", "")),
            verdict=str(s.get("verdict", "")),
            citations=[int(c) for c in s.get("citations") or []],
        )
        for s in row.sentences or []
    ]
    citations = [
        PublicCitation(
            n=int(c.get("n", 0)),
            title=c.get("title") or "",
            url=c.get("url"),
            date=c.get("date"),
            quote=c.get("quote") or "",
        )
        for c in row.citations or []
    ]
    return PublicAnswerShare(
        question=row.question, answer_md=row.answer_md, sentences=sentences, citations=citations
    )


__all__ = [
    "create_share",
    "find_live_share",
    "hash_token",
    "list_shares",
    "public_snapshot",
    "revoke_share",
]
