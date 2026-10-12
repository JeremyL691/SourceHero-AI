"""Engine / session plumbing.

Every module gets its engine from here so that the pool is shared, and so tests can
override `get_db` without any module knowing about it.
"""

from __future__ import annotations

from collections.abc import Generator
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker


def normalize_db_url(url: str) -> str:
    """Accept every spelling of a Postgres URL and return the psycopg3 driver form."""
    if not url:
        return url
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix) :]
    return url


def psycopg_url(url: str) -> str:
    """The same URL without the SQLAlchemy driver marker, for direct psycopg use."""
    return normalize_db_url(url).replace("postgresql+psycopg://", "postgresql://", 1)


@lru_cache(maxsize=1)
def get_engine(url: str | None = None) -> Engine:
    from app.core.config import get_settings

    settings = get_settings()
    target = normalize_db_url(url or settings.database_url)
    return create_engine(
        target,
        pool_pre_ping=True,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
    )


@lru_cache(maxsize=1)
def get_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), autoflush=False, expire_on_commit=False)


def SessionLocal() -> Session:
    """A session on the shared engine. Callers own closing it."""
    return get_sessionmaker()()


@lru_cache(maxsize=1)
def get_ledger_engine(url: str | None = None) -> Engine:
    """A second pool for LLM bookkeeping: the run ledger, budget reservations and the cache.

    Ledger writes commit on their own transaction, so a business rollback never erases them.
    They must not share the request pool: a request holding a connection while it waits for a
    ledger connection from that same pool deadlocks once requests reach the pool size.
    """
    from app.core.config import get_settings

    settings = get_settings()
    target = normalize_db_url(url or settings.database_url)
    return create_engine(
        target,
        pool_pre_ping=True,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
    )


@lru_cache(maxsize=1)
def get_ledger_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(bind=get_ledger_engine(), autoflush=False, expire_on_commit=False)


def LedgerSessionLocal() -> Session:
    """A session on the ledger pool, for one independent bookkeeping transaction."""
    return get_ledger_sessionmaker()()


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency yielding a request-scoped session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def reset_engine_cache() -> None:
    """Tests that point at a different database call this first."""
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()
    get_ledger_engine.cache_clear()
    get_ledger_sessionmaker.cache_clear()


__all__ = [
    "LedgerSessionLocal",
    "SessionLocal",
    "get_db",
    "get_engine",
    "get_sessionmaker",
    "normalize_db_url",
    "psycopg_url",
    "reset_engine_cache",
]
