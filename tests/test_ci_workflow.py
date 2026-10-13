"""Static checks for the CI workflow: pinned tooling and the expected jobs."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
EXACT_VERSION = re.compile(r"^\d+\.\d+\.\d+$")


def _load() -> dict[str, Any]:
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _steps(workflow: dict[str, Any]) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    for job in workflow["jobs"].values():
        steps.extend(job.get("steps", []))
    return steps


def test_docker_build_and_e2e_jobs_exist() -> None:
    jobs = _load()["jobs"]
    assert "docker-build" in jobs
    assert "e2e" in jobs
    # e2e must run the same entry point developers use, not a re-implementation.
    e2e_runs = [s.get("run", "") for s in jobs["e2e"]["steps"]]
    assert any(run.strip() == "make e2e" for run in e2e_runs)


def test_setup_uv_is_pinned_to_an_exact_version() -> None:
    uv_steps = [s for s in _steps(_load()) if str(s.get("uses", "")).startswith("astral-sh/setup-uv")]
    assert uv_steps, "expected at least one astral-sh/setup-uv step"
    for step in uv_steps:
        version = str(step.get("with", {}).get("version", ""))
        assert EXACT_VERSION.match(version), f"setup-uv version is not exact: {version!r}"


def test_no_step_uses_latest() -> None:
    workflow = _load()
    offenders: list[str] = []
    for job in workflow["jobs"].values():
        for service in (job.get("services") or {}).values():
            if "latest" in str(service.get("image", "")):
                offenders.append(str(service["image"]))
        for step in job.get("steps", []):
            for value in (step.get("uses"), step.get("with"), step.get("run")):
                if value is not None and "latest" in str(value):
                    offenders.append(str(value))
    assert offenders == []


def test_compose_declares_env_file_healthchecks_shm_and_required_password():
    """OPS-9: the env file reaches api and worker, worker and web have health checks, the
    database gets enough shared memory for parallel queries, and its password is required."""
    from pathlib import Path

    import yaml

    compose = yaml.safe_load((Path(__file__).resolve().parents[1] / "docker-compose.yml").read_text("utf-8"))
    services = compose["services"]
    for name in ("api", "worker"):
        env_files = services[name]["env_file"]
        assert any(
            isinstance(entry, dict) and entry.get("path") == ".env" and entry.get("required") is False
            for entry in env_files
        ), f"{name} must read .env when it exists"
    assert "--healthcheck" in " ".join(services["worker"]["healthcheck"]["test"])
    assert "healthcheck" in services["web"]
    assert services["db"]["shm_size"] == "1gb"
    assert ":?" in services["db"]["environment"]["POSTGRES_PASSWORD"], (
        "the password must be required, not defaulted"
    )
