"""scripts/db.sh must stop and reset the database it manages (K9.5 acceptance: db-down must make the tests fail)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROFILES = ("app", "prod", "s3")


def test_db_down_and_reset_name_every_compose_profile(tmp_path):
    """`docker compose down` skips services whose profile is not named, and the db service sits in the
    app and prod profiles. Without the profiles, `make db-down` left the database running and
    `make db-reset` kept its volume, so the fail-closed check passed against a live database."""
    log = tmp_path / "docker.log"
    stub = tmp_path / "docker"
    stub.write_text(f'#!/usr/bin/env bash\necho "$@" >> "{log}"\n', encoding="utf-8")
    stub.chmod(0o755)
    env = {**os.environ, "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}"}

    for action in ("down", "reset"):
        subprocess.run(["bash", str(ROOT / "scripts" / "db.sh"), action], check=True, env=env, cwd=ROOT)

    calls = log.read_text(encoding="utf-8").splitlines()
    stopping = [line for line in calls if " down" in line]
    assert stopping, calls
    for line in stopping:
        for profile in PROFILES:
            assert f"--profile {profile}" in line, line
    assert any(" down -v" in line for line in stopping), calls
