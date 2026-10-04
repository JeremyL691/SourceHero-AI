"""Tests for test harness safety invariants (TEST-5)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from tests.conftest import _check_safe_test_db, pytest_collection_modifyitems


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
