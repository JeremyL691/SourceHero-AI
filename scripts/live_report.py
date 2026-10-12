"""Figures for docs/dev/LIVE_LLM_REPORT.md, each read from the database it names (plan K3.6).

    uv run python scripts/live_report.py --db-url <url>

Prints one JSON object. Every key comes from the query in QUERIES, so a number in the
report can be traced to a line here and rerun against the same database.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from sqlalchemy import create_engine, text

QUERIES = {
    "llm_calls_by_status": "SELECT status, count(*) AS n FROM llm_calls GROUP BY status ORDER BY status",
    "llm_calls_by_task_and_status": (
        "SELECT task, status, count(*) AS n FROM llm_calls GROUP BY task, status ORDER BY task, status"
    ),
    "llm_ok_tokens": (
        "SELECT coalesce(sum(prompt_tokens), 0) AS prompt_tokens, "
        "coalesce(sum(completion_tokens), 0) AS completion_tokens, "
        "coalesce(round(avg(latency_ms)), 0) AS mean_latency_ms FROM llm_calls WHERE status = 'ok'"
    ),
    "llm_failures": (
        "SELECT left(error, 120) AS error, count(*) AS n FROM llm_calls "
        "WHERE status <> 'ok' AND error IS NOT NULL GROUP BY left(error, 120) ORDER BY n DESC LIMIT 8"
    ),
    "claims_by_extractor": "SELECT extractor, count(*) AS n FROM claims GROUP BY extractor ORDER BY extractor",
    "relations_by_method_and_label": (
        "SELECT method, relation, count(*) AS n FROM claim_relations GROUP BY method, relation "
        "ORDER BY method, relation"
    ),
    "contradictions": "SELECT count(*) AS n FROM claim_relations WHERE relation = 'contradicts'",
    "wiki_pages_by_status_and_compile_method": (
        "SELECT status, compile_method, count(*) AS n FROM wiki_pages GROUP BY status, compile_method "
        "ORDER BY status, compile_method"
    ),
    "extract_claims_jobs_by_status": (
        "SELECT status, count(*) AS n FROM jobs WHERE kind = 'extract_claims' GROUP BY status ORDER BY status"
    ),
    "quotes_rejected_unaligned": (
        "SELECT coalesce(sum((result->>'rejected_unaligned')::int), 0) AS n FROM jobs "
        "WHERE kind = 'extract_claims' AND result IS NOT NULL"
    ),
    "claims_created_by_extract_jobs": (
        "SELECT coalesce(sum((result->>'created')::int), 0) AS n FROM jobs "
        "WHERE kind = 'extract_claims' AND result IS NOT NULL"
    ),
}


def main() -> int:
    parser = argparse.ArgumentParser(description="figures for the live LLM report")
    parser.add_argument("--db-url", required=True, help="the database to read, e.g. sourcehero_live")
    parser.add_argument("--cassette", default="evals/cassettes/live_demo.jsonl")
    parser.add_argument("--probe", default=".dev/logs/live-demo.json")
    args = parser.parse_args()

    engine = create_engine(args.db_url)
    figures: dict[str, object] = {}
    with engine.connect() as conn:
        for name, sql in QUERIES.items():
            figures[name] = [dict(row._mapping) for row in conn.execute(text(sql))]
    engine.dispose()

    cassette = Path(args.cassette)
    figures["cassette_rows"] = sum(1 for _ in cassette.open(encoding="utf-8")) if cassette.exists() else 0
    probe = Path(args.probe)
    figures["ask"] = json.loads(probe.read_text(encoding="utf-8")) if probe.exists() else []
    print(json.dumps(figures, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
