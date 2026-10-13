"""Backup and restore: scripts/backup.sh and scripts/restore.sh on a seeded database.

The scripts run as real subprocesses against the test database. Backups go under pytest's
tmp_path (BACKUP_ROOT), and the restore goes into a throwaway database that is dropped after.
"""

from __future__ import annotations

import os
import re
import subprocess
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.core.db import normalize_db_url
from app.demo.seed import drain, seed_demo
from app.models.user import User
from app.models.wiki import WikiPage
from app.wiki.compiler import compile_page
from app.wiki.topics import plan_topics

pytestmark = [pytest.mark.slow, pytest.mark.db]

REPO_ROOT = Path(__file__).resolve().parents[1]
RESTORE_DB = "sourcehero_restore_test"
COUNT_TABLES = ("documents", "document_versions", "claims", "claim_relations", "wiki_pages")
SHA_LINE = re.compile(r"^[0-9a-f]{64}\s+(db\.dump|storage\.tar)$", re.MULTILINE)


def _source_url() -> str:
    return normalize_db_url(os.environ["DATABASE_URL"])


def _url_for(database: str) -> str:
    return make_url(_source_url()).set(database=database).render_as_string(hide_password=False)


def _counts(engine: Engine) -> dict[str, int]:
    with engine.connect() as conn:
        return {t: conn.execute(text(f"SELECT count(*) FROM {t}")).scalar_one() for t in COUNT_TABLES}


def _alembic_versions(engine: Engine) -> list[str]:
    with engine.connect() as conn:
        return sorted(r[0] for r in conn.execute(text("SELECT version_num FROM alembic_version")))


def _drop_database(name: str) -> None:
    admin = create_engine(_url_for("postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    finally:
        admin.dispose()


def _run(args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", *args], cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=300
    )


@pytest.fixture(scope="module")
def seeded_source(engine, truncate_after_module) -> Iterator[Engine]:
    """One user's demo corpus, compiled into wiki pages, committed for the subprocess to see."""
    # Enqueue without running handlers inline, so drain() below runs them in order.
    settings = get_settings()
    inline = settings.jobs_inline
    settings.jobs_inline = False
    try:
        factory = sessionmaker(bind=engine)
        with factory() as session:
            user = User(email=f"backup-{uuid.uuid4().hex[:8]}@example.test", auth_provider="local")
            session.add(user)
            session.flush()
            seed_demo(session, user.id)
            drain(session)
            plan_topics(session, user.id)
            for page in session.execute(select(WikiPage).where(WikiPage.user_id == user.id)).scalars():
                compile_page(session, page)
            session.commit()
        yield engine
    finally:
        settings.jobs_inline = inline


def test_backup_then_restore_matches_source(seeded_source: Engine, tmp_path: Path) -> None:
    source_counts = _counts(seeded_source)
    assert source_counts["documents"] > 0 and source_counts["claims"] > 0, source_counts
    assert source_counts["wiki_pages"] > 0, source_counts
    source_versions = _alembic_versions(seeded_source)
    assert source_versions, "alembic_version is empty in the source"

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "marker.txt").write_text("storage marker")
    env = {
        **os.environ,
        "DATABASE_URL": _source_url(),
        "BACKUP_ROOT": str(tmp_path / "backups"),
        "DATA_DIR": str(data_dir),
    }

    backup = _run(["scripts/backup.sh"], env)
    assert backup.returncode == 0, backup.stderr
    backup_dir = Path(backup.stdout.strip().splitlines()[-1])
    assert backup_dir.parent == tmp_path / "backups", backup_dir
    assert (backup_dir / "db.dump").stat().st_size > 0
    manifest = (backup_dir / "MANIFEST.txt").read_text()
    assert len(SHA_LINE.findall(manifest)) == 2, manifest
    assert f"database: {make_url(_source_url()).database}" in manifest
    assert "password" not in manifest.lower()
    storage_list = subprocess.run(
        ["tar", "-tf", str(backup_dir / "storage.tar")], capture_output=True, text=True, check=True
    ).stdout
    assert "marker.txt" in storage_list

    _drop_database(RESTORE_DB)
    try:
        restore = _run(["scripts/restore.sh", str(backup_dir), RESTORE_DB], env)
        assert restore.returncode == 0, restore.stderr
        assert "left in place" in restore.stdout

        restored = create_engine(_url_for(RESTORE_DB))
        try:
            assert _counts(restored) == source_counts
            assert _alembic_versions(restored) == source_versions
        finally:
            restored.dispose()
    finally:
        _drop_database(RESTORE_DB)


def test_restore_refuses_missing_dump(tmp_path: Path) -> None:
    result = _run(["scripts/restore.sh", str(tmp_path), RESTORE_DB], dict(os.environ))
    assert result.returncode != 0
    assert "dump file missing" in result.stderr
