"""Pure-function tests for scripts/loadtest.py (no network)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "loadtest.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("loadtest_script", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


loadtest = _load_module()


def test_nearest_rank_percentile_on_1_to_100() -> None:
    values = [float(v) for v in range(1, 101)]
    assert loadtest.nearest_rank_percentile(values, 50) == 50.0
    assert loadtest.nearest_rank_percentile(values, 95) == 95.0
    assert loadtest.nearest_rank_percentile(values, 99) == 99.0
    assert loadtest.nearest_rank_percentile(values, 100) == 100.0


def test_nearest_rank_percentile_small_and_unsorted() -> None:
    # n=4: p50 rank = ceil(2) = 2 -> 20; p95 rank = ceil(3.8) = 4 -> 40
    assert loadtest.nearest_rank_percentile([40.0, 10.0, 30.0, 20.0], 50) == 20.0
    assert loadtest.nearest_rank_percentile([40.0, 10.0, 30.0, 20.0], 95) == 40.0
    # a single value is every percentile; p0 clamps to the first value
    assert loadtest.nearest_rank_percentile([7.0], 99) == 7.0
    assert loadtest.nearest_rank_percentile([10.0, 20.0], 0) == 10.0


def test_nearest_rank_percentile_empty_returns_none() -> None:
    assert loadtest.nearest_rank_percentile([], 50) is None


def test_summarize_matches_hand_computed_values() -> None:
    summary = loadtest.summarize([10.0, 20.0, 30.0, 40.0], errors=1, duration_s=2.0)
    assert summary["requests"] == 4
    assert summary["errors"] == 1
    assert summary["error_rate"] == pytest.approx(0.25)
    assert summary["throughput_rps"] == pytest.approx(2.0)
    assert summary["p50_ms"] == 20.0
    assert summary["p95_ms"] == 40.0
    assert summary["p99_ms"] == 40.0


def test_summarize_with_no_requests() -> None:
    summary = loadtest.summarize([], errors=0, duration_s=5.0)
    assert summary["requests"] == 0
    assert summary["error_rate"] == 0.0
    assert summary["throughput_rps"] == 0.0
    assert summary["p50_ms"] is None
    assert summary["p99_ms"] is None


def test_failing_scenarios_flags_rate_above_limit() -> None:
    summaries = {
        "search": {"error_rate": 0.02},
        "verify": {"error_rate": 0.0},
    }
    assert loadtest.failing_scenarios(summaries, 0.01) == ["search"]


def test_failing_scenarios_passes_at_exactly_the_limit() -> None:
    summaries = {"ask": {"error_rate": 0.01}, "captures": {"error_rate": 0.005}}
    assert loadtest.failing_scenarios(summaries, 0.01) == []


def test_main_requires_token_without_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LOADTEST_TOKEN", raising=False)
    assert loadtest.main(["--scenarios", "search"]) == 2
