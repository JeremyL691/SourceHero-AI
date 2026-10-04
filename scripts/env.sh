#!/usr/bin/env bash
# Parse .env WITHOUT sourcing it (a .env can hold JSON and spaces that break `source`)
# and export only the keys this project's scripts need.
# shellcheck disable=SC2046
[[ -n "${SOURCEHERO_ENV_LOADED:-}" ]] && return 0 2>/dev/null || true

_env_file="${ENV_FILE:-.env}"
_env_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
[[ -f "${_env_dir}/${_env_file}" ]] || _env_file=".env.example"
_env_path="${_env_dir}/${_env_file}"

_sget() {
  grep -E "^$1=" "${_env_path}" | tail -1 | cut -d= -f2- || true
}

export DB_PORT="$(_sget DB_PORT)"
export API_PORT="$(_sget API_PORT)"
export WEB_PORT="$(_sget WEB_PORT)"
export E2E_API_PORT="$(_sget E2E_API_PORT)"
export E2E_WEB_PORT="$(_sget E2E_WEB_PORT)"
export DATABASE_URL="$(_sget DATABASE_URL)"
export TEST_DATABASE_URL="$(_sget TEST_DATABASE_URL)"
export EVAL_DATABASE_URL="$(_sget EVAL_DATABASE_URL)"
export E2E_DATABASE_URL="$(_sget E2E_DATABASE_URL)"
export CORS_ORIGINS="$(_sget CORS_ORIGINS)"
export PUBLIC_WEB_URL="$(_sget PUBLIC_WEB_URL)"
export DATA_DIR="$(_sget DATA_DIR)"
export SOURCEHERO_ENV="$(_sget SOURCEHERO_ENV)"
export SOURCEHERO_SECRET_KEY="$(_sget SOURCEHERO_SECRET_KEY)"

# The postgres superuser this project authenticates as, used by scripts/db_ensure_databases.py
export PGUSER="$(_sget PGUSER)"; export PGUSER="${PGUSER:-sourcehero}"
export PGPASSWORD="$(_sget PGPASSWORD)"; export PGPASSWORD="${PGPASSWORD:-sourcehero_dev}"

# docker compose reads .env from the compose file's directory
export COMPOSE_ENV_FILE="${_env_path}"
export SOURCEHERO_ENV_LOADED=1
