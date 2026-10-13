#!/usr/bin/env bash
# Start the dashboard.
#
# An explicit PORT in the environment wins over .env: the e2e run passes its own port,
# and reading .env afterwards would silently start it on the dev port.
# The bind address comes from WEB_HOST, never $HOSTNAME: bash sets HOSTNAME to the
# machine name by itself, which bound the dev server off loopback (ACC-3).
set -euo pipefail

export PORT="${PORT:-}"
if [[ -z "$PORT" ]]; then
  # shellcheck source=scripts/env.sh
  source "$(dirname "$0")/env.sh"
fi
export PORT="${PORT:-${WEB_PORT:-3787}}"
# The standalone server.js reads HOSTNAME.
export HOSTNAME="${WEB_HOST:-127.0.0.1}"

cd "$(dirname "$0")/../dashboard-web"

case "${1:-dev}" in
  dev)
    exec npm run dev -- -p "$PORT" -H "$HOSTNAME"
    ;;
  start)
    [[ -d .next/standalone ]] || {
      echo "run_web: no standalone build; run 'npm run build' first" >&2
      exit 1
    }
    # The standalone output excludes these two by design.
    cp -r .next/static .next/standalone/.next/static 2>/dev/null || true
    mkdir -p .next/standalone/.next
    cp -r public .next/standalone/public 2>/dev/null || true
    cd .next/standalone
    exec node server.js
    ;;
  *) echo "usage: scripts/run_web.sh {dev|start}" >&2; exit 2 ;;
esac
