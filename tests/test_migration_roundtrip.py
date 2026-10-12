"""0010_security: upgrade, downgrade one step, upgrade again, and the schema follows each step."""

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


def test_0010_security_upgrade_downgrade_upgrade(engine):
    cfg = _config(engine.url.render_as_string(hide_password=False))

    def users_has_token_version() -> bool:
        return "token_version" in {c["name"] for c in inspect(engine).get_columns("users")}

    def rate_limits_exists() -> bool:
        return "rate_limits" in inspect(engine).get_table_names()

    command.upgrade(cfg, "head")
    assert users_has_token_version()
    assert rate_limits_exists()

    command.downgrade(cfg, "-1")
    assert not users_has_token_version()
    assert not rate_limits_exists()

    command.upgrade(cfg, "head")
    assert users_has_token_version()
    assert rate_limits_exists()
