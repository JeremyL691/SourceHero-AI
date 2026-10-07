#!/usr/bin/env bash
# End-to-end run: api + worker + a production web build, then Playwright.
#
# Everything runs offline and with `LLM_PROVIDER=fake` / `EMBEDDING_PROVIDER=hashing`,
# so the suite is deterministic and never calls a paid API.
#
# Only PIDs recorded in .dev/pids/e2e-*.pid are ever signalled -- other projects are
# running on this machine and their ports must not be touched.
set -euo pipefail

cd "$(dirname "$0")/.."

mkdir -p .dev/logs .dev/pids

# 1. Ports. Confirms the ports and writes to .env before sourcing.
bash scripts/ports.sh

# shellcheck source=scripts/env.sh
source scripts/env.sh

E2E_API_PORT="${E2E_API_PORT:-8797}"
E2E_WEB_PORT="${E2E_WEB_PORT:-3797}"
E2E_DB_NAME="sourcehero_e2e"
E2E_DB_URL="postgresql+psycopg://sourcehero:sourcehero_dev@127.0.0.1:${DB_PORT}/${E2E_DB_NAME}"

cleanup() {
  echo ""
  echo "e2e: stopping the e2e processes"
  for pidfile in .dev/pids/e2e-*.pid; do
    [[ -e "$pidfile" ]] || continue
    pid="$(cat "$pidfile" 2>/dev/null || true)"
    if [[ -n "$pid" ]]; then
      local pgid
      pgid="$(ps -o pgid= -p "$pid" 2>/dev/null | tr -d ' ' || true)"
      if [[ -n "$pgid" && "$pgid" -gt 1 ]]; then
        kill -TERM -- "-$pgid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
        for _ in $(seq 1 25); do
          kill -0 "$pid" 2>/dev/null || break
          sleep 0.2
        done
        kill -KILL -- "-$pgid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null || true
      fi
    fi
    rm -f "$pidfile"
  done
}
trap cleanup EXIT

start() {
  local name="$1"; shift
  local pidfile=".dev/pids/e2e-${name}.pid"
  local logfile=".dev/logs/e2e-${name}.log"
  if command -v setsid >/dev/null 2>&1; then
    setsid "$@" >"$logfile" 2>&1 &
    echo $! >"$pidfile"
  else
    python3 -c 'import sys, subprocess
logfile = open(sys.argv[1], "a")
p = subprocess.Popen(sys.argv[2:], stdout=logfile, stderr=logfile, start_new_session=True)
print(p.pid)
' "$logfile" "$@" > "$pidfile"
  fi
  echo "e2e: started ${name} (pid $(cat "$pidfile")) -> ${logfile}"
}

wait_for() {
  local url="$1" label="$2" tries="${3:-120}"
  for _ in $(seq 1 "$tries"); do
    if curl -fsS "$url" >/dev/null 2>&1; then
      echo "e2e: ${label} is up"
      return 0
    fi
    sleep 0.5
  done
  echo "e2e: ${label} did not come up; see .dev/logs/e2e-*.log" >&2
  return 1
}

# 2. A database of its own, so a failed run can never leave the dev data behind.
echo "e2e: preparing ${E2E_DB_NAME}"
uv run python scripts/db_ensure_e2e.py --db-url "$E2E_DB_URL"
uv run alembic -x dburl="$E2E_DB_URL" upgrade head

# 3. api + worker against that database.
start api env \
  DATABASE_URL="$E2E_DB_URL" \
  SOURCEHERO_ENV=test \
  API_PORT="$E2E_API_PORT" \
  LLM_PROVIDER=fake \
  EMBEDDING_PROVIDER=hashing \
  JOBS_INLINE=false \
  JOB_DEBOUNCE_SCALE=0.02 \
  CORS_ORIGINS="http://127.0.0.1:${E2E_WEB_PORT},http://localhost:${E2E_WEB_PORT}" \
  PUBLIC_WEB_URL="http://127.0.0.1:${E2E_WEB_PORT}" \
  uv run python -m app.serve

start worker env \
  DATABASE_URL="$E2E_DB_URL" \
  SOURCEHERO_ENV=test \
  LLM_PROVIDER=fake \
  EMBEDDING_PROVIDER=hashing \
  JOBS_INLINE=false \
  JOB_DEBOUNCE_SCALE=0.02 \
  uv run python -m app.jobs.worker --concurrency 2

wait_for "http://127.0.0.1:${E2E_API_PORT}/healthz" "api"

# 4. A production web build, started standalone.
echo "e2e: building the dashboard"
(
  cd dashboard-web
  NEXT_PUBLIC_API_URL="http://127.0.0.1:${E2E_API_PORT}" npm run build
)

start web env \
  PORT="$E2E_WEB_PORT" \
  WEB_PORT="$E2E_WEB_PORT" \
  HOSTNAME=127.0.0.1 \
  NEXT_PUBLIC_API_URL="http://127.0.0.1:${E2E_API_PORT}" \
  bash scripts/run_web.sh start

wait_for "http://127.0.0.1:${E2E_WEB_PORT}/" "web"

# 5. The browser.
cd dashboard-web
if ! npx playwright install chromium >/dev/null 2>&1; then
  # A failed download is not fatal if a usable browser is already present.
  echo "e2e: chromium download skipped (using the local install)"
fi

echo "e2e: running the suite against http://127.0.0.1:${E2E_WEB_PORT}"
E2E_WEB_URL="http://127.0.0.1:${E2E_WEB_PORT}" E2E_API_URL="http://127.0.0.1:${E2E_API_PORT}" \
  npx playwright test "$@"
