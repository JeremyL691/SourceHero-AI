#!/bin/sh
# Container entrypoint for the API/worker image.
# RUN_MIGRATIONS=1 applies Alembic migrations before the main process starts.
set -eu

if [ "${RUN_MIGRATIONS:-0}" = "1" ]; then
  alembic upgrade head
fi

exec "$@"
