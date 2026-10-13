"""0010_security and 0011_features: each step upgrades and downgrades exactly its own schema."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

pytestmark = [pytest.mark.slow, pytest.mark.db]

REPO_ROOT = Path(__file__).resolve().parents[1]


def _config(url: str) -> Config:
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    # migrations/env.py takes the URL from `-x dburl=...`, so the test never reaches
    # for whatever `.env` points at.
    cfg.cmd_opts = argparse.Namespace(x=[f"dburl={url}"])
    return cfg


def test_each_step_upgrades_and_downgrades_its_own_schema(engine):
    cfg = _config(engine.url.render_as_string(hide_password=False))

    def inspector():
        return inspect(engine)

    def has_0011() -> bool:
        tables = inspector().get_table_names()
        reliability = "reliability" in {c["name"] for c in inspector().get_columns("sources")}
        return reliability and "share_links" in tables

    def has_0010() -> bool:
        tables = inspector().get_table_names()
        token_version = "token_version" in {c["name"] for c in inspector().get_columns("users")}
        return token_version and "rate_limits" in tables

    command.upgrade(cfg, "head")
    assert has_0010() and has_0011()

    command.downgrade(cfg, "-1")  # 0011 only
    assert not has_0011()
    assert has_0010()

    command.downgrade(cfg, "0009_revision_method")  # 0010
    assert not has_0010()

    command.upgrade(cfg, "head")
    assert has_0010() and has_0011()
