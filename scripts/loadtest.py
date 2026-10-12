"""Load smoke test for the SourceHero v2 API (K7.7).

Runs each scenario for ``--duration`` seconds with ``--concurrency`` asyncio workers.
Scenarios: search, verify, ask (non-streaming), captures (text notes).

Usage::

    uv run python scripts/loadtest.py --base-url http://127.0.0.1:8787 \
        --token <PAT> --concurrency 10 --duration 60 \
        --scenarios search,verify,ask,captures [--json-out out.json] [--max-error-rate 0.01]

The token is read from ``--token`` or the ``LOADTEST_TOKEN`` environment variable and is
never printed. Prerequisites: the API is running and ``make demo`` has seeded data.

Percentiles use the nearest-rank method: for n sorted values, the p-th percentile is the
value at 1-based rank ceil(p/100 * n). Throughput is requests divided by the run duration.
A request counts as an error when it raises or returns a non-2xx status.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import sys
import time
import uuid
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import httpx

DEFAULT_MAX_ERROR_RATE = 0.01
ALL_SCENARIOS = ("search", "verify", "ask", "captures")
REQUEST_TIMEOUT_S = 30.0

# scenario name -> (path, query params, payload factory)
PayloadFn = Callable[[], dict[str, Any]]
ScenarioSpec = tuple[str, dict[str, str], PayloadFn]


def _search_payload() -> dict[str, Any]:
    return {"query": "HX-2 energy density", "k": 10}


def _verify_payload() -> dict[str, Any]:
    return {"text": "The HX-2 cell delivers 450 Wh/kg."}


def _ask_payload() -> dict[str, Any]:
    return {"question": "What is the energy density of the HX-2 cell?"}


def _captures_payload() -> dict[str, Any]:
    return {"text": f"loadtest note {uuid.uuid4().hex}"}


SCENARIOS: dict[str, ScenarioSpec] = {
    "search": ("/v1/search", {}, _search_payload),
    "verify": ("/v1/verify", {}, _verify_payload),
    # The ask endpoint reads stream from the query string, not the body.
    "ask": ("/v1/ask", {"stream": "false"}, _ask_payload),
    "captures": ("/v1/captures", {}, _captures_payload),
}


def nearest_rank_percentile(values: Sequence[float], pct: float) -> float | None:
    """Return the nearest-rank percentile of ``values`` (None when empty).

    The rank is ceil(pct/100 * n), clamped to [1, n]; the result is the value at that
    1-based rank in the sorted list. For 1..100, p50 is 50, p95 is 95 and p99 is 99.
    """
    if not values:
        return None
    ordered = sorted(values)
    rank = math.ceil(pct / 100.0 * len(ordered))
    rank = min(max(rank, 1), len(ordered))
    return float(ordered[rank - 1])


def summarize(latencies_ms: Sequence[float], errors: int, duration_s: float) -> dict[str, Any]:
    """Build the per-scenario summary.

    ``latencies_ms`` holds one entry per completed request (successful or failed);
    ``errors`` is how many of them failed. Error rate is errors / requests (0.0 when no
    requests ran). Throughput is requests / duration_s.
    """
    requests = len(latencies_ms)
    error_rate = errors / requests if requests else 0.0
    throughput = requests / duration_s if duration_s > 0 else 0.0
    return {
        "requests": requests,
        "errors": errors,
        "error_rate": round(error_rate, 6),
        "throughput_rps": round(throughput, 3),
        "p50_ms": _round_or_none(nearest_rank_percentile(latencies_ms, 50)),
        "p95_ms": _round_or_none(nearest_rank_percentile(latencies_ms, 95)),
        "p99_ms": _round_or_none(nearest_rank_percentile(latencies_ms, 99)),
    }


def failing_scenarios(summaries: dict[str, dict[str, Any]], max_error_rate: float) -> list[str]:
    """Return the names whose error rate is strictly above ``max_error_rate``."""
    return [name for name, s in summaries.items() if float(s["error_rate"]) > max_error_rate]


def _round_or_none(value: float | None) -> float | None:
    return None if value is None else round(value, 2)


async def _run_scenario(
    client: httpx.AsyncClient,
    path: str,
    params: dict[str, str],
    payload_fn: PayloadFn,
    concurrency: int,
    duration_s: float,
) -> tuple[list[float], int]:
    """Drive ``concurrency`` workers until the deadline; return (latencies_ms, errors)."""
    latencies: list[float] = []
    errors = 0
    deadline = time.monotonic() + duration_s

    async def worker() -> None:
        nonlocal errors
        while time.monotonic() < deadline:
            started = time.perf_counter()
            failed = False
            try:
                resp = await client.post(path, json=payload_fn(), params=params)
                failed = not (200 <= resp.status_code < 300)
            except httpx.HTTPError:
                failed = True
            latencies.append((time.perf_counter() - started) * 1000.0)
            if failed:
                errors += 1

    await asyncio.gather(*(worker() for _ in range(concurrency)))
    return latencies, errors


async def run_all(
    base_url: str,
    token: str,
    concurrency: int,
    duration_s: float,
    scenarios: Sequence[str],
) -> dict[str, dict[str, Any]]:
    """Run the requested scenarios one after another and return their summaries."""
    headers = {"Authorization": f"Bearer {token}"}
    results: dict[str, dict[str, Any]] = {}
    for name in scenarios:
        path, params, payload_fn = SCENARIOS[name]
        async with httpx.AsyncClient(base_url=base_url, headers=headers, timeout=REQUEST_TIMEOUT_S) as client:
            started = time.monotonic()
            latencies, errors = await _run_scenario(client, path, params, payload_fn, concurrency, duration_s)
            elapsed = time.monotonic() - started
        results[name] = summarize(latencies, errors, elapsed)
    return results


def _print_table(results: dict[str, dict[str, Any]], concurrency: int) -> None:
    header = (
        f"{'scenario':<10} {'conc':>5} {'reqs':>7} {'errors':>7} {'err%':>7} "
        f"{'rps':>8} {'p50ms':>9} {'p95ms':>9} {'p99ms':>9}"
    )
    print(header)
    print("-" * len(header))
    for name, s in results.items():
        print(
            f"{name:<10} {concurrency:>5} {s['requests']:>7} {s['errors']:>7} "
            f"{s['error_rate'] * 100:>6.2f}% {s['throughput_rps']:>8.2f} "
            f"{_fmt(s['p50_ms']):>9} {_fmt(s['p95_ms']):>9} {_fmt(s['p99_ms']):>9}"
        )


def _fmt(value: float | None) -> str:
    return "-" if value is None else f"{value:.2f}"


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SourceHero API load smoke test")
    parser.add_argument("--base-url", default="http://127.0.0.1:8787")
    parser.add_argument("--token", default=None, help="PAT (or set LOADTEST_TOKEN)")
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--duration", type=float, default=60.0, help="seconds per scenario")
    parser.add_argument(
        "--scenarios",
        default=",".join(ALL_SCENARIOS),
        help=f"comma-separated subset of: {', '.join(ALL_SCENARIOS)}",
    )
    parser.add_argument("--json-out", type=Path, default=None)
    parser.add_argument("--max-error-rate", type=float, default=DEFAULT_MAX_ERROR_RATE)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point. Returns 0 when the script ran, 1 when a threshold failed, 2 on bad input."""
    args = _parse_args(argv)
    token = args.token or os.environ.get("LOADTEST_TOKEN", "")
    if not token:
        print("error: provide --token or set LOADTEST_TOKEN", file=sys.stderr)
        return 2
    names = [n.strip() for n in args.scenarios.split(",") if n.strip()]
    unknown = [n for n in names if n not in SCENARIOS]
    if unknown or args.concurrency < 1 or args.duration <= 0:
        print("error: invalid --scenarios, --concurrency or --duration", file=sys.stderr)
        return 2

    results = asyncio.run(run_all(args.base_url.rstrip("/"), token, args.concurrency, args.duration, names))
    _print_table(results, args.concurrency)

    if args.json_out is not None:
        report = {
            "base_url": args.base_url,
            "concurrency": args.concurrency,
            "duration_s": args.duration,
            "max_error_rate": args.max_error_rate,
            "scenarios": results,
        }
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    failed = failing_scenarios(results, args.max_error_rate)
    for name in failed:
        print(
            f"FAILED: {name} error rate {results[name]['error_rate'] * 100:.2f}% "
            f"> limit {args.max_error_rate * 100:.2f}%"
        )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
