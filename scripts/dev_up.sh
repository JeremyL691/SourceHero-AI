#!/usr/bin/env bash
# Start api + worker + web in the background. PIDs land in .dev/pids/, logs in .dev/logs/.
# Nothing outside .dev/pids/ is ever touched.
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck source=scripts/env.sh
source scripts/env.sh

mkdir -p .dev/logs .dev/pids
# shellcheck source=scripts/env.sh
source scripts/env.sh

start() {
  local name="$1"; shift
  local pidfile=".dev/pids/${name}.pid"
  if [[ -f "$pidfile" ]] && kill -0 "$(cat "$pidfile")" 2>/dev/null; then
    echo "dev_up: $name already running (pid $(cat "$pidfile"))"; return 0
  fi
  "$@" >".dev/logs/${name}.log" 2>&1 &
  echo $! >"$pidfile"
  echo "dev_up: started $name (pid $!) -> .dev/logs/${name}.log"
}

start api uv run python -m app.serve
start worker uv run python -m app.jobs.worker

echo "dev_up: waiting for http://127.0.0.1:${API_PORT}/healthz ..."
for i in $(seq 1 60); do
  if curl -fsS "http://127.0.0.1:${API_PORT}/healthz" >/dev/null 2>&1; then
    echo "dev_up: api healthy after ${i}s"
    break
  fi
  sleep 1
done
curl -fsS "http://127.0.0.1:${API_PORT}/healthz" || {
  echo "dev_up: api did not become healthy; see .dev/logs/api.log" >&2
  exit 1
}

start web bash scripts/run_web.sh dev
echo "dev_up: web on http://127.0.0.1:${WEB_PORT} (wait for Next.js to finish compiling)"
echo "dev_up: logs in .dev/logs/, pids in .dev/pids/. Stop with: make stop"
