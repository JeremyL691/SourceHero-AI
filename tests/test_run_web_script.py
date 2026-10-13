"""scripts/run_web.sh must bind the dev server to loopback."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _bound_host(tmp_path: Path, **env: str) -> str:
    log = tmp_path / "npm.log"
    stub = tmp_path / "npm"
    stub.write_text(f'#!/usr/bin/env bash\necho "$@" > "{log}"\n', encoding="utf-8")
    stub.chmod(0o755)
    base = {k: v for k, v in os.environ.items() if k not in ("HOSTNAME", "WEB_HOST")}
    full = {**base, "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}", "PORT": "3999", **env}
    subprocess.run(["bash", str(ROOT / "scripts" / "run_web.sh"), "dev"], check=True, env=full, cwd=ROOT)
    args = log.read_text(encoding="utf-8").split()
    return args[args.index("-H") + 1]


def test_dev_server_binds_loopback_although_bash_sets_hostname(tmp_path):
    """bash sets $HOSTNAME to the machine name on its own; reading it bound `make dev` off loopback."""
    assert _bound_host(tmp_path) == "127.0.0.1"
    assert _bound_host(tmp_path, HOSTNAME="laptop.local") == "127.0.0.1"


def test_web_host_overrides_the_bind_address(tmp_path):
    assert _bound_host(tmp_path, WEB_HOST="0.0.0.0") == "0.0.0.0"
