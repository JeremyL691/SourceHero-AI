#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../dashboard-web"
# shellcheck source=scripts/env.sh
source ../scripts/env.sh

case "${1:-dev}" in
  dev)
    exec npm run dev -- -p "$WEB_PORT" -H 127.0.0.1
    ;;
  start)
    [[ -d .next/standalone ]] || { echo "run_web: no standalone build; run 'npm run build' first" >&2; exit 1; }
    cp -r .next/static .next/standalone/.next/static 2>/dev/null || true
    mkdir -p .next/standalone/.next
    cp -r public .next/standalone/public 2>/dev/null || true
    cd .next/standalone
    export PORT="$WEB_PORT" HOSTNAME=127.0.0.1
    exec node server.js
    ;;
  *) echo "usage: scripts/run_web.sh {dev|start}" >&2; exit 2 ;;
esac
