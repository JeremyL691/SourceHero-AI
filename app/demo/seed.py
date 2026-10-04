"""Seeding the demo corpus.

Everything downstream -- the verifier's expected verdicts, the conflict map, the
belief diff, the wiki -- depends on this corpus being exactly what the plan specifies.
So the seed is idempotent and asserts its own invariants rather than trusting them.

    python -m app.demo.seed --email demo@sourcehero.local --password ...
"""

from __future__ import annotations

import argparse
import uuid
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import time as clock
from app.core.db import SessionLocal
from app.core.logging import configure_logging, get_logger
from app.demo.corpus import CorpusFile, CorpusVersion, load_corpus, to_extracted
from app.ingestion.pipeline import ensure_user
from app.ingestion.versions import upsert_document_version
from app.jobs import queue as q
from app.models.chunk import Chunk
from app.models.document import Document, DocumentVersion
from app.models.source import Source

log = get_logger(__name__)

DEMO_DOMAIN = "demo.sourcehero.local"
_MAX_DRAIN_JOBS = 10_000


@dataclass
class SeedStats:
    sources: int = 0
    documents: int = 0
    versions: int = 0
    unchanged: int = 0
    jobs: int = 0
    extra: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "sources": self.sources,
            "documents": self.documents,
            "versions": self.versions,
            "unchanged": self.unchanged,
            "jobs": self.jobs,
            **self.extra,
        }


def _source_for(db: Session, user_id: uuid.UUID, corpus_file: CorpusFile) -> Source:
    """One source per corpus file, matched on its `corpus_id`.

    Matched on the id rather than on the URL or the title: a note has no URL, and its
    stored name comes from the corpus `source` field, not its title -- so keying on
    either would silently create a duplicate source on every run.
    """
    existing = db.execute(
        select(Source).where(
            Source.user_id == user_id,
            Source.config["corpus_id"].astext == corpus_file.id,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    source = Source(
        user_id=user_id,
        source_type=corpus_file.source_type,
        name=corpus_file.source if corpus_file.source_type == "note" else corpus_file.title,
        url=corpus_file.url,
        canonical_url=corpus_file.url,
        config={"demo": True, "corpus_id": corpus_file.id},
        status="active",
    )
    db.add(source)
    db.flush()
    return source


def seed_demo(db: Session, user_id: uuid.UUID) -> SeedStats:
    """Store every corpus file as versions, and queue indexing for the new ones.

    Idempotent: re-running creates no new documents, versions or jobs.
    """
    stats = SeedStats()
    corpus = load_corpus()

    for corpus_file in corpus:
        source = _source_for(db, user_id, corpus_file)
        stats.sources = db.execute(
            select(func.count()).select_from(Source).where(Source.user_id == user_id)
        ).scalar_one()

        for version in corpus_file.versions:
            extracted = to_extracted(corpus_file, version)
            result = upsert_document_version(
                db,
                source=source,
                extracted=extracted,
                origin="demo",
                # World time for this version is the date in the corpus, not "now":
                # the temporal model is only meaningful if the demo's timeline is the
                # timeline the corpus describes.
                effective_at=_at_midnight_utc(version),
            )
            if result.is_new_or_changed and result.version is not None:
                stats.versions += 1
                q.enqueue(
                    db,
                    "index_version",
                    {"version_id": result.version.id},
                    user_id=user_id,
                    now=clock.utcnow(),
                )
                stats.jobs += 1
            else:
                stats.unchanged += 1

    db.commit()
    stats.documents = db.execute(
        select(func.count()).select_from(Document).where(Document.user_id == user_id)
    ).scalar_one()
    stats.versions = db.execute(
        select(func.count()).select_from(DocumentVersion).where(DocumentVersion.user_id == user_id)
    ).scalar_one()
    return stats


def _at_midnight_utc(version: CorpusVersion):
    from datetime import UTC, datetime

    return datetime(version.date.year, version.date.month, version.date.day, tzinfo=UTC)


def queue_demo_seed(db: Session, user_id: uuid.UUID) -> list[int]:
    """Seed and return the queued job ids -- what `POST /v1/demo/seed` returns."""
    seed_demo(db, user_id)
    db.flush()
    job_ids = list(
        db.execute(select(q.Job.id).where(q.Job.user_id == user_id, q.Job.status == "queued")).scalars()
    )
    return job_ids


def drain(db: Session | None = None) -> int:
    """Run every queued job to completion, in-process.

    Used by the seed CLI so debounced follow-ups finish before the process exits and
    the demo has something to show the moment it returns. Ignores `run_after`, so a
    debounced job scheduled 60 seconds out still runs.
    """
    from app.jobs.handlers import register_all  # noqa: F401 - registration side effect
    from app.jobs.queue import dequeue
    from app.jobs.registry import JobContext, NonRetryableJobError, get_handler

    register_all()
    own_db = db is None
    session = db or SessionLocal()
    processed = 0
    try:
        while processed < _MAX_DRAIN_JOBS:
            job = dequeue(session, worker="seed-drain", drain=True)
            if job is None:
                break
            try:
                result = get_handler(job.kind)(
                    JobContext(db=session, job=job, user_id=job.user_id),
                    dict(job.payload or {}),
                )
                q.complete(session, job.id, result or {})
                session.commit()
            except Exception as exc:  # noqa: BLE001 - one bad job must not hang the CLI
                session.rollback()
                q.fail(
                    session,
                    job.id,
                    str(exc) or type(exc).__name__,
                    retryable=not isinstance(exc, NonRetryableJobError),
                )
                session.commit()
                log.warning("seed drain: job %s failed: %s", job.kind, exc)
            processed += 1
    finally:
        if own_db:
            session.close()
    return processed


def summary(db: Session, user_id: uuid.UUID) -> dict:
    """Counts the UI and the acceptance checks read."""
    out = {
        "documents": db.execute(
            select(func.count()).select_from(Document).where(Document.user_id == user_id)
        ).scalar_one(),
        "versions": db.execute(
            select(func.count()).select_from(DocumentVersion).where(DocumentVersion.user_id == user_id)
        ).scalar_one(),
        "chunks": db.execute(
            select(func.count()).select_from(Chunk).where(Chunk.user_id == user_id)
        ).scalar_one(),
        "sources": db.execute(
            select(func.count()).select_from(Source).where(Source.user_id == user_id)
        ).scalar_one(),
    }
    # Later milestones append their counts here (claims, conflicts, wiki pages).
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="seed the demo corpus")
    parser.add_argument("--email", default="demo@sourcehero.local")
    parser.add_argument("--password", default="demo-password-123")
    parser.add_argument("--display-name", default="Demo user")
    parser.add_argument("--no-drain", action="store_true", help="queue only, do not run")
    args = parser.parse_args()

    configure_logging()
    from app.core.security import hash_password
    from app.models.user import User

    db = SessionLocal()
    try:
        user, created = ensure_user(db, args.email, args.password, display_name=args.display_name)
        if not created:
            row = db.execute(select(User).where(User.id == user.id)).scalar_one()
            row.password_hash = hash_password(args.password)
            db.commit()
        log.info("user %s (%s)", user.email, "created" if created else "existing")

        stats = seed_demo(db, user.id)
        # Safe here and only here: `make demo` runs a single process with no separate
        # worker consuming the queue. See seed_demo_corpus_job for why the HTTP path
        # must not do this.
        if not args.no_drain:
            processed = drain(db)
            log.info("drained %d job(s)", processed)

        counts = summary(db, user.id)
        print("\nDemo corpus")
        print("-----------")
        for key, value in counts.items():
            print(f"  {key:12s} {value}")
        if stats.jobs:
            print(f"  {'queued':12s} {stats.jobs}")
        print(f"\nSign in as {args.email} / {args.password}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
