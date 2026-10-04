"""Session-level advisory locks.

`cluster_claims`, `judge_relations`, `reconcile_document`, `plan_topics` and
`compile_wiki_page` all read other workers' *uncommitted* rows. Without a lock, worker A
clustering a claim cannot see the claims worker B just inserted, and each concludes
that two claims from the same ingest are unrelated.

`pg_advisory_lock` is session-scoped, so it is released explicitly in a `finally` --
transaction-scoped locking would be released at commit and would not span the work.

`pg_try_advisory_lock` rather than the blocking variant: a stale lock should slow the
pipeline down, not stop it. The caller decides what to do when it cannot get in.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import text
from sqlalchemy.orm import Session


@contextmanager
def session_lock(db: Session, name: str) -> Iterator[bool]:
    """Hold an advisory lock for the rest of this database session.

    Yields True when the lock was taken. On release, a rollback is issued because
    `pg_advisory_unlock` cannot run inside a failed transaction.
    """
    acquired = False
    try:
        acquired = bool(
            db.execute(text("SELECT pg_try_advisory_lock(hashtext(:name))"), {"name": name}).scalar()
        )
        yield acquired
    finally:
        if acquired:
            try:
                db.execute(text("SELECT pg_advisory_unlock(hashtext(:name))"), {"name": name})
            except Exception:  # noqa: BLE001 - the session may already be unusable
                db.rollback()


__all__ = ["session_lock"]
