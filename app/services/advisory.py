"""Transaction-scoped advisory locks.

`cluster_claims`, `judge_relations`, `reconcile_document`, `plan_topics`,
`compile_wiki_page` and `lint_wiki` all synchronize on per-user keys.
Using `pg_try_advisory_xact_lock` guarantees the lock is held exactly until the
current transaction commits or rolls back, avoiding stale locks even when
handlers abort or raise exceptions.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session


def try_xact_lock(db: Session, name: str) -> bool:
    """Attempt to acquire a transaction-level advisory lock.

    Returns True if acquired. Automatically released when the current transaction ends.
    """
    return bool(
        db.execute(
            text("SELECT pg_try_advisory_xact_lock(hashtext(:name))"),
            {"name": name},
        ).scalar()
    )


__all__ = ["try_xact_lock"]
