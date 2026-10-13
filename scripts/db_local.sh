#!/usr/bin/env bash
# Homebrew Postgres + pgvector fallback, used only when Docker is unavailable
# and .dev/db_mode contains "local".
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck source=scripts/env.sh
source scripts/env.sh

PG_BIN="${PG_BIN:-/opt/homebrew/opt/postgresql@17/bin}"
if [[ ! -x "$PG_BIN/pg_ctl" ]]; then
  for cand in /opt/homebrew/opt/postgresql@*/bin /usr/local/opt/postgresql@*/bin /opt/homebrew/bin /usr/local/bin; do
    if [[ -x "$cand/pg_ctl" ]]; then PG_BIN="$cand"; break; fi
  done
fi
export PATH="$PG_BIN:$PATH"
PGDATA="$(pwd)/.dev/pg"
LOG="$(pwd)/.dev/logs/pg.log"

mkdir -p .dev/logs "$PGDATA"

is_up() { "$PG_BIN/pg_ctl" -D "$PGDATA" status >/dev/null 2>&1; }

case "${1:-up}" in
  up)
    if ! is_up; then
      if [[ ! -f "$PGDATA/PG_VERSION" ]]; then
        "$PG_BIN/initdb" -D "$PGDATA" -U "$PGUSER" --auth=trust >/dev/null
      fi
      "$PG_BIN/pg_ctl" -D "$PGDATA" -o "-p $DB_PORT -k /tmp" -l "$LOG" start
    fi
    for _ in $(seq 1 30); do
      "$PG_BIN/pg_isready" -h 127.0.0.1 -p "$DB_PORT" -U "$PGUSER" >/dev/null 2>&1 && break
      sleep 1
    done
    "$PG_BIN/createdb" -h 127.0.0.1 -p "$DB_PORT" -U "$PGUSER" sourcehero 2>/dev/null || true
    "$PG_BIN/psql" -q -h 127.0.0.1 -p "$DB_PORT" -U "$PGUSER" -d sourcehero -f infra/db/init/01-init.sql >/dev/null 2>&1 || true
    echo "db_local: up on 127.0.0.1:$DB_PORT"
    ;;
  down)
    is_up && "$PG_BIN/pg_ctl" -D "$PGDATA" -m fast stop || echo "db_local: already down"
    ;;
  reset)
    "$PG_BIN/pg_ctl" -D "$PGDATA" -m immediate stop >/dev/null 2>&1 || true
    rm -rf "$PGDATA"; mkdir -p "$PGDATA"
    "$PG_BIN/initdb" -D "$PGDATA" -U "$PGUSER" --auth=trust >/dev/null
    "$PG_BIN/pg_ctl" -D "$PGDATA" -o "-p $DB_PORT -k /tmp" -l "$LOG" start
    for _ in $(seq 1 30); do
      "$PG_BIN/pg_isready" -h 127.0.0.1 -p "$DB_PORT" -U "$PGUSER" >/dev/null 2>&1 && break
      sleep 1
    done
    "$PG_BIN/createdb" -h 127.0.0.1 -p "$DB_PORT" -U "$PGUSER" sourcehero
    "$PG_BIN/psql" -q -h 127.0.0.1 -p "$DB_PORT" -U "$PGUSER" -d sourcehero -f infra/db/init/01-init.sql >/dev/null
    echo "db_local: reset on 127.0.0.1:$DB_PORT"
    ;;
  ps) "$PG_BIN/pg_ctl" -D "$PGDATA" status || true ;;
  *) echo "usage: scripts/db_local.sh {up|down|reset|ps}" >&2; exit 2 ;;
esac
