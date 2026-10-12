"""Tests for test harness safety invariants (TEST-5)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tests.conftest import _check_safe_test_db, pytest_collection_modifyitems

REPO_ROOT = Path(__file__).resolve().parents[1]
UNREACHABLE_DB = "postgresql+psycopg://sourcehero:x@127.0.0.1:1/sourcehero_test"


def test_unsafe_database_name_raises():
    with pytest.raises(RuntimeError, match="Refusing to DROP SCHEMA"):
        _check_safe_test_db("postgresql://postgres:postgres@localhost:5432/sourcehero")

    with pytest.raises(RuntimeError, match="Refusing to DROP SCHEMA"):
        _check_safe_test_db("postgresql://postgres:postgres@localhost:5432/production")


def test_safe_database_names_pass():
    _check_safe_test_db("postgresql://postgres:postgres@localhost:5432/sourcehero_test")
    _check_safe_test_db("postgresql://postgres:postgres@localhost:5432/sourcehero_e2e")
    _check_safe_test_db("postgresql+psycopg://postgres:postgres@localhost:5432/my_project_test")


def test_db_unreachable_only_skips_db_tests(monkeypatch):
    monkeypatch.setattr("tests.conftest._db_reachable", lambda: False)
    monkeypatch.delenv("REQUIRE_DB", raising=False)

    class FakeItem:
        def __init__(self, name: str, keywords: set[str], fixturenames: set[str]):
            self.name = name
            self.keywords = keywords
            self.fixturenames = fixturenames
            self.markers = []

        def add_marker(self, marker):
            self.markers.append(marker)

    db_item = FakeItem("test_db_op", {"db"}, set())
    fixture_item = FakeItem("test_uses_db", set(), {"db"})
    unit_item = FakeItem("test_pure_unit", set(), set())

    items = [db_item, fixture_item, unit_item]
    config = MagicMock()

    pytest_collection_modifyitems(config, items)

    assert len(db_item.markers) == 1
    assert db_item.markers[0].name == "skip"
    assert len(fixture_item.markers) == 1
    assert fixture_item.markers[0].name == "skip"
    assert len(unit_item.markers) == 0  # Pure unit test is NOT skipped


def _run_probe(require_db: str | None) -> subprocess.CompletedProcess[str]:
    """Run one DB-backed test in a child pytest against a closed port.

    The `-k` filter keeps the child on `test_probe_needs_db` only, so it never re-enters these tests.
    """
    env = {**os.environ, "TEST_DATABASE_URL": UNREACHABLE_DB, "DATABASE_URL": UNREACHABLE_DB}
    env.pop("REQUIRE_DB", None)
    if require_db is not None:
        env["REQUIRE_DB"] = require_db
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            str(Path(__file__).resolve()),
            "-k",
            "probe_needs_db",
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )


def test_require_db_fails_the_run_when_database_unreachable():
    proc = _run_probe("1")
    output = proc.stdout + proc.stderr
    assert proc.returncode != 0, output
    assert "database required" in output, output


def test_db_tests_still_skip_without_require_db():
    proc = _run_probe(None)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "1 skipped" in proc.stdout, proc.stdout


def test_probe_needs_db(db):
    """Target of the child runs above. Passing it means the database was reachable."""
