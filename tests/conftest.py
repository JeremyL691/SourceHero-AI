"""Test fixtures.

Everything runs against a real Postgres: the schema uses generated columns, partial
indexes, HNSW and `tsvector`, none of which SQLite could stand in for.

Two session shapes exist, and the difference matters:

* `db` -- wraps each test in a transaction and rolls it back. Fast, isolated, default.
  Because `now()` is frozen at transaction start inside it, tests exercising queue
  backoff or the reaper pass an explicit `now` (or use `fresh_db`).
* `fresh_db` -- really commits, then truncates. For anything where a second
  connection must see the rows: concurrency, the worker, MCP.

Environment variables are set *before* importing `app`, because `get_settings` is
cached on first use and `.env` would otherwise win.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _env_value(key: str, default: str = "") -> str:
    """Read one key from .env without sourcing the file."""
    env_file = REPO_ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip() == key:
                return v.strip()
    return default


TEST_DB_URL = os.environ.get("TEST_DATABASE_URL") or _env_value(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://sourcehero:sourcehero_dev@127.0.0.1:54339/sourcehero_test",
)

os.environ.update(
    {
        "SOURCEHERO_ENV": "test",
        "JOBS_INLINE": "true",
        "EMBEDDING_PROVIDER": "hashing",
        "LLM_PROVIDER": "fake",
        "RESPECT_ROBOTS": "false",
        "PER_HOST_MIN_INTERVAL_SECONDS": "0",
        "JOB_DEBOUNCE_SCALE": "0",
        "ALLOW_REGISTRATION": "true",
        "DATABASE_URL": TEST_DB_URL,
    }
)

# Legacy v1 tests wait here while their replacements are written; M4 deletes them.
collect_ignore_glob = ["_legacy/*"]

DB_UNAVAILABLE = False

# Tables truncated by `fresh_db`, leaf-first is unnecessary because of CASCADE.
TRUNCATE_TABLES = (
    "lint_findings",
    "wiki_links",
    "wiki_page_claims",
    "wiki_page_revisions",
    "wiki_pages",
    "topics",
    "belief_diffs",
    "watches",
    "claim_events",
    "claim_relations",
    "claim_clusters",
    "claims",
    "verifications",
    "answers",
    "llm_cache",
    "llm_calls",
    "oauth_states",
    "provider_credentials",
    "eval_runs",
    "chunks",
    "ingestion_runs",
    "document_versions",
    "documents",
    "sources",
    "scheduled_job_runs",
    "scheduled_jobs",
    "jobs",
    "collection_items",
    "collections",
    "item_tags",
    "tags",
    "document_flags",
    "api_tokens",
    "app_settings",
    "users",
)


def _db_reachable() -> bool:
    from sqlalchemy import create_engine, text

    from app.core.db import normalize_db_url

    try:
        engine = create_engine(normalize_db_url(TEST_DB_URL), pool_pre_ping=True)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
        return True
    except Exception:  # noqa: BLE001
        return False


@pytest.fixture(scope="session", autouse=True)
def _prepare_database() -> Iterator[None]:
    """Drop the public schema and rebuild it from migrations, once per session."""
    global DB_UNAVAILABLE
    if not _db_reachable():
        DB_UNAVAILABLE = True
        yield
        return

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, text

    from app.core.db import normalize_db_url

    engine = create_engine(normalize_db_url(TEST_DB_URL), pool_pre_ping=True)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
    engine.dispose()

    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", normalize_db_url(TEST_DB_URL).replace("%", "%%"))
    command.upgrade(cfg, "head")

    yield


@pytest.fixture(scope="session")
def engine():
    from sqlalchemy import create_engine

    from app.core.db import normalize_db_url

    if DB_UNAVAILABLE:
        pytest.skip("database unreachable")
    return create_engine(normalize_db_url(TEST_DB_URL), pool_pre_ping=True)


@pytest.fixture
def db(engine):
    """Transaction-per-test. A `commit()` inside the test releases a savepoint."""
    from sqlalchemy.orm import Session

    if DB_UNAVAILABLE:
        pytest.skip("database unreachable")
    conn = engine.connect()
    trans = conn.begin()
    session = Session(bind=conn, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        trans.rollback()
        conn.close()


@pytest.fixture
def fresh_db(engine):
    """Really commits, then truncates. For tests a second connection must observe."""
    from sqlalchemy.orm import Session

    if DB_UNAVAILABLE:
        pytest.skip("database unreachable")
    session = Session(bind=engine)
    try:
        yield session
        session.commit()
    finally:
        session.close()
    _truncate(engine)


def _truncate(engine) -> None:
    from sqlalchemy import text

    existing = {
        r[0]
        for r in engine.connect().execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'"))
    }
    present = [t for t in TRUNCATE_TABLES if t in existing]
    if present:
        with engine.begin() as conn:
            conn.execute(text(f"TRUNCATE {', '.join(present)} RESTART IDENTITY CASCADE"))


@pytest.fixture
def make_client(db):
    """Build an authenticated TestClient bound to the transaction-scoped session."""
    from fastapi.testclient import TestClient

    from app.core.db import get_db
    from app.main import create_app

    created: list = []

    def _make(
        session=None,
        *,
        email: str | None = None,
        password: str = "test-password-123",
        admin: bool = False,
    ):
        """Build a client bound to `session`, optionally registered.

        A registered user is demoted to non-admin unless `admin=True`: the *first*
        account in an empty schema automatically owns the instance, so leaving that
        in place would let isolation tests pass for entirely the wrong reason.
        """
        active = session if session is not None else db
        app = create_app()
        app.dependency_overrides[get_db] = lambda: active
        client = TestClient(app)
        created.append(app)
        if email is not None:
            resp = client.post("/v1/auth/register", json={"email": email, "password": password})
            assert resp.status_code == 201, resp.text
            client.headers.update({"Authorization": f"Bearer {resp.json()['access_token']}"})
            if not admin:
                from app.models.user import User

                row = active.get(User, uuid.UUID(resp.json()["user"]["id"]))
                if row is not None and row.is_admin:
                    row.is_admin = False
                    active.flush()
        return client

    yield _make
    for app in created:
        app.dependency_overrides.clear()


@pytest.fixture
def client(make_client):
    """An anonymous client bound to the transaction-scoped session."""
    with make_client() as c:
        yield c


@pytest.fixture
def user_factory(make_client):
    """Register a user and return an authenticated client plus their record."""

    def _make(email: str | None = None, password: str = "test-password-123", **kwargs):

        email = email or f"u{uuid.uuid4().hex[:12]}@test.local"
        app_client = make_client(email=email, password=password)
        # `make_client` already authenticated; fetch the record the same way the app does.
        me = app_client.get("/v1/auth/me")
        assert me.status_code == 200, me.text
        return app_client, me.json()

    return _make


@pytest.fixture
def other_client(make_client):
    """A second, independent user -- for tenant-isolation assertions."""
    return make_client(email=f"other{uuid.uuid4().hex[:12]}@test.local")


@pytest.fixture
def no_inline_jobs(monkeypatch):
    """Turn off JOBS_INLINE for tests that drive the queue by hand.

    The test session sets JOBS_INLINE=true so pipeline tests get end-to-end
    execution for free -- but a test that enqueues and then wants to *dequeue*
    must not have the handler run at enqueue time.
    """
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "jobs_inline", False)
    return True


@pytest.fixture
def fake_llm():
    """Scriptable FakeProvider: `script(task, [responses])` pops responses in FIFO order."""
    from app.llm.providers.fake import FakeProvider

    return FakeProvider()


@pytest.fixture
def freeze_time(monkeypatch):
    """Pin `utcnow()`; call `.set(...)` / `.advance(...)` to move the clock."""

    from datetime import UTC, datetime

    class Clock:
        def __init__(self) -> None:
            self.now = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)

        def __call__(self, *args, **kwargs):
            return self.now

        def set(self, value) -> None:
            self.now = value

        def advance(self, **kwargs) -> None:
            from datetime import timedelta

            self.now = self.now + timedelta(**kwargs)

    clock = Clock()
    monkeypatch.setattr("app.core.time.utcnow", clock)
    return clock


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "network: needs internet")
    config.addinivalue_line("markers", "model: downloads ML models")
    config.addinivalue_line("markers", "slow: >10s")
    config.addinivalue_line("markers", "db: needs a reachable Postgres")


def pytest_collection_modifyitems(config: pytest.Config, items: list) -> None:
    """Skip DB tests when Postgres is unreachable -- see the terminal warning."""
    if _db_reachable():
        return
    skip = pytest.mark.skip(reason="database unreachable")
    for item in items:
        item.add_marker(skip)


def pytest_terminal_summary(terminalreporter, exitstatus, config) -> None:
    if DB_UNAVAILABLE:
        terminalreporter.write_line(
            "\n*** WARNING: the test database is unreachable, so every test was skipped. "
            "Start Postgres with `make db-up`. ***\n",
            yellow=True,
        )
