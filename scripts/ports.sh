#!/usr/bin/env bash
# Port preflight: every port this project uses is read from .env (never hardcoded at
# call sites). If a port is already taken -- by another project on this machine --
# walk upward until a free one is found and write it back to .env.
#
# We never kill anything. We only pick different ports.
set -euo pipefail
cd "$(dirname "$0")/.."

ENV_FILE="${ENV_FILE:-.env}"

if [[ ! -f "$ENV_FILE" ]]; then
  cp .env.example "$ENV_FILE"
fi

WEB_ENV="dashboard-web/.env.local"

# Read KEY=VALUE from the env file without sourcing it.
env_get() { grep -E "^$1=" "$ENV_FILE" | tail -1 | cut -d= -f2- || true; }

# Rewrite KEY in place, or append it when absent. Portable across BSD and GNU sed.
set_key() {
  local file="$1" key="$2" val="$3"
  if grep -qE "^${key}=" "$file"; then
    # BSD sed (macOS) needs the extension after -i; GNU sed does not accept it.
    sed -i '' -E "s|^${key}=.*$|${key}=${val}|" "$file"
  else
    printf '%s=%s\n' "$key" "$val" >>"$file"
  fi
}

set_env() { set_key "$ENV_FILE" "$1" "$2"; }
sub_env() { [[ -f "$WEB_ENV" ]] && set_key "$WEB_ENV" "$1" "$2"; return 0; }

OUR_CONTAINER="sourcehero-v2-db"

# A port held by *our own* db container is not a conflict -- otherwise every run of
# ports.sh would walk the port upward every time the stack is already up.
ours() {
  local port="$1"
  command -v docker >/dev/null 2>&1 || return 1
  docker port "$OUR_CONTAINER" 5432/tcp 2>/dev/null \
    | grep -qE "[:.]$port\$"
}

port_busy() {
  lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1 && ! ours "$1"
}

find_free() {
  local port="$1" tries=0
  while port_busy "$port"; do
    port=$((port + 1))
    tries=$((tries + 1))
    if (( tries > 200 )); then
      echo "ports.sh: could not find a free port starting at $1" >&2
      return 1
    fi
  done
  echo "$port"
}

ensure() {
  # ensure <KEY> <default>
  local key="$1" default="$2" current
  current="$(env_get "$key")"
  [[ -n "$current" ]] || current="$default"
  local free
  free="$(find_free "$current")"
  if [[ "$free" != "$current" ]]; then
    echo "ports.sh: $key was $current but is busy; using $free" >&2
    set_env "$key" "$free"
  fi
  echo "$free"
}

DB_PORT_NEW="$(ensure DB_PORT 54339)"
API_PORT_NEW="$(ensure API_PORT 8787)"
WEB_PORT_NEW="$(ensure WEB_PORT 3787)"
E2E_API_PORT_NEW="$(ensure E2E_API_PORT 8797)"
E2E_WEB_PORT_NEW="$(ensure E2E_WEB_PORT 3797)"

USER="$(env_get DB_USER || true)"
USER="${USER:-sourcehero}"
PASS="$(env_get DB_PASSWORD || true)"
PASS="${PASS:-sourcehero_dev}"

# Rewrite every derived URL/allow-list so the chosen ports are actually used.
set_env DATABASE_URL "postgresql+psycopg://${USER}:${PASS}@127.0.0.1:${DB_PORT_NEW}/sourcehero"
set_env TEST_DATABASE_URL "postgresql+psycopg://${USER}:${PASS}@127.0.0.1:${DB_PORT_NEW}/sourcehero_test"
set_env EVAL_DATABASE_URL "postgresql+psycopg://${USER}:${PASS}@127.0.0.1:${DB_PORT_NEW}/sourcehero_eval"
set_env E2E_DATABASE_URL "postgresql+psycopg://${USER}:${PASS}@127.0.0.1:${DB_PORT_NEW}/sourcehero_e2e"
set_env CORS_ORIGINS "http://127.0.0.1:${WEB_PORT_NEW},http://localhost:${WEB_PORT_NEW},http://127.0.0.1:${E2E_WEB_PORT_NEW},http://localhost:${E2E_WEB_PORT_NEW}"
set_env PUBLIC_WEB_URL "http://127.0.0.1:${WEB_PORT_NEW}"
set_env OAUTH_REDIRECT_URI "http://127.0.0.1:${API_PORT_NEW}/v1/settings/llm/oauth/callback"
sub_env NEXT_PUBLIC_API_URL "http://127.0.0.1:${API_PORT_NEW}"

echo "ports: DB=$DB_PORT_NEW API=$API_PORT_NEW WEB=$WEB_PORT_NEW e2e_api=$E2E_API_PORT_NEW e2e_web=$E2E_WEB_PORT_NEW"