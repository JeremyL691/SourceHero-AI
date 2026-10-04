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

    target = normalize_db_url(url or get_settings().database_url)
    return create_engine(target, pool_pre_ping=True, pool_size=10, max_overflow=20)


@lru_cache(maxsize=1)
def get_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), autoflush=False, expire_on_commit=False)


def SessionLocal() -> Session:
    """A session on the shared engine. Callers own closing it."""
    return get_sessionmaker()()


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


__all__ = [
    "SessionLocal",
    "get_db",
    "get_engine",
    "get_sessionmaker",
    "normalize_db_url",
    "psycopg_url",
    "reset_engine_cache",
]