#!/usr/bin/env bash
# The whole pipeline on OpenRouter's free models, in a database of its own.
#
# 1. Reset sourcehero_live and migrate it.
# 2. Seed the demo corpus with the real model. Every answer is recorded in
#    evals/cassettes/live_demo.jsonl, which is committed, so the run can be replayed.
# 3. Ask the Ask endpoint three questions and keep what each stream did.
# 4. Seed the same corpus with the fake provider into sourcehero_live_fake, to compare.
#
# The daily request budget for the live seed is 300 (LLM_DAILY_REQUEST_BUDGET). The script
# starts no server: it calls the app in-process, so it binds no port and signals no PID.
set -euo pipefail

unset PYTHONPATH || true
cd "$(dirname "$0")/.."
REPO_ROOT="$PWD"
mkdir -p .dev/logs evals/cassettes

# shellcheck source=scripts/env.sh
source scripts/env.sh

LIVE_DB="sourcehero_live"
FAKE_DB="sourcehero_live_fake"
live_url="postgresql+psycopg://sourcehero:sourcehero_dev@127.0.0.1:${DB_PORT}/${LIVE_DB}"
fake_url="postgresql+psycopg://sourcehero:sourcehero_dev@127.0.0.1:${DB_PORT}/${FAKE_DB}"
cassette="${REPO_ROOT}/evals/cassettes/live_demo.jsonl"
email="demo@sourcehero.local"
password="demo-password-123"

echo "live: resetting ${LIVE_DB}"
uv run python scripts/db_ensure_e2e.py --db-url "$live_url"
DATABASE_URL="$live_url" uv run alembic upgrade head

echo "live: seeding with the real model (recording to ${cassette})"
started=$(date +%s)
DATABASE_URL="$live_url" \
LLM_PRESET=openrouter \
LLM_CASSETTE_MODE=record \
LLM_CASSETTE_PATH="$cassette" \
LLM_DAILY_REQUEST_BUDGET=300 \
EMBEDDING_PROVIDER=hashing \
RERANK_PROVIDER=none \
uv run python -m app.demo.seed --email "$email" --password "$password"
echo "live: seed finished in $(( $(date +%s) - started ))s"

echo "live: asking the Ask endpoint three questions"
DATABASE_URL="$live_url" \
LLM_PRESET=openrouter \
LLM_CASSETTE_MODE=record \
LLM_CASSETTE_PATH="$cassette" \
LLM_DAILY_REQUEST_BUDGET=300 \
EMBEDDING_PROVIDER=hashing \
RERANK_PROVIDER=none \
PYTHONPATH=. uv run python scripts/live_demo_probe.py

echo "fake: seeding ${FAKE_DB} for comparison (no network)"
uv run python scripts/db_ensure_e2e.py --db-url "$fake_url"
DATABASE_URL="$fake_url" uv run alembic upgrade head
DATABASE_URL="$fake_url" \
LLM_PROVIDER=fake \
EMBEDDING_PROVIDER=hashing \
RERANK_PROVIDER=none \
uv run python -m app.demo.seed --email "$email" --password "$password"

echo "live: done. The report's numbers come from: uv run python scripts/live_report.py --db-url <url>"
