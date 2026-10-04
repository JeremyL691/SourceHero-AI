#!/usr/bin/env bash
# Dispatch to docker compose or the local Postgres cluster, depending on .dev/db_mode.
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck source=scripts/env.sh
source scripts/env.sh

MODE_FILE=".dev/db_mode"
MODE="docker"
[[ -f "$MODE_FILE" ]] && MODE="$(cat "$MODE_FILE")"

action="${1:-up}"
if [[ "$MODE" == "local" ]]; then
  exec bash scripts/db_local.sh "$action"
fi

case "$action" in
  up)     docker compose up -d db ;;
  down)   docker compose down ;;
  reset)  docker compose down -v; docker compose up -d db ;;
  ps)     docker compose ps db ;;
  *) echo "usage: scripts/db.sh {up|down|reset|ps}" >&2; exit 2 ;;
esac
