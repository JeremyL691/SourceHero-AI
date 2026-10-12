#!/usr/bin/env bash
# Restore a backup made by scripts/backup.sh.
#
# Usage: scripts/restore.sh <backup-dir> [target_db] [--force]
#   target_db defaults to "<main database name>_restore".
#   Restoring into the main database (the one named by DATABASE_URL in .env or the
#   environment) needs --force, and so does restoring over an existing target.
#   With --force, storage.tar is extracted into DATA_DIR; without it, it is left in place.
# Requires: pg_restore and psql on PATH.
set -euo pipefail

die() { echo "restore.sh: $*" >&2; exit 1; }
usage() { echo "usage: scripts/restore.sh <backup-dir> [target_db] [--force]" >&2; exit 2; }

DIR=""
TARGET=""
FORCE=0
for arg in "$@"; do
  case "$arg" in
    --force) FORCE=1 ;;
    -h|--help) usage ;;
    -*) die "unknown option: $arg" ;;
    *)
      if [[ -z "$DIR" ]]; then DIR="$arg"
      elif [[ -z "$TARGET" ]]; then TARGET="$arg"
      else usage
      fi
      ;;
  esac
done
[[ -n "$DIR" ]] || usage

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

[[ -f "$DIR/db.dump" ]] || die "dump file missing: $DIR/db.dump"

# Parse DATABASE_URL (environment, else .env) and DATA_DIR into shell assignments.
CONF="$(uv run --quiet python - <<'PY'
import os
import shlex
import sys
from pathlib import Path

from sqlalchemy.engine import make_url


def value(key: str) -> str:
    if os.environ.get(key):
        return os.environ[key]
    env_file = Path(".env")
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip() == key:
                return v.strip().strip("'\"")
    return ""


def parse_url(raw: str):
    for prefix in ("postgres://", "postgresql://"):
        if raw.startswith(prefix):
            raw = "postgresql+psycopg://" + raw[len(prefix):]
    return make_url(raw)


raw = value("DATABASE_URL")
if not raw:
    sys.exit("DATABASE_URL is not set (environment or .env)")
url = parse_url(raw)
dotenv = Path(".env")
env_db = ""
for line in (dotenv.read_text().splitlines() if dotenv.exists() else []):
    if line.startswith("DATABASE_URL="):
        env_db = parse_url(line.split("=", 1)[1].strip().strip("'\"")).database or ""
print(f"DB_HOST={shlex.quote(url.host or '127.0.0.1')}")
print(f"DB_PORT={shlex.quote(str(url.port or 5432))}")
print(f"DB_USER={shlex.quote(url.username or '')}")
print(f"DB_NAME={shlex.quote(url.database or '')}")
print(f"DB_PASSWORD={shlex.quote(url.password or '')}")
print(f"DOTENV_DB={shlex.quote(env_db)}")
print(f"DATA_DIR_RAW={shlex.quote(value('DATA_DIR') or './data')}")
PY
)" || die "could not read database settings"
eval "$CONF"

DATA_DIR="$DATA_DIR_RAW"
[[ "$DATA_DIR" == /* ]] || DATA_DIR="$REPO_ROOT/$DATA_DIR"

TARGET="${TARGET:-${DB_NAME}_restore}"
[[ "$TARGET" =~ ^[A-Za-z0-9_]+$ ]] || die "target database name must match [A-Za-z0-9_]+"

MAIN_DBS=("$DB_NAME")
[[ -n "$DOTENV_DB" ]] && MAIN_DBS+=("$DOTENV_DB")
for main in "${MAIN_DBS[@]}"; do
  if [[ "$TARGET" == "$main" && "$FORCE" -ne 1 ]]; then
    die "refusing to restore into the main database '$main' without --force"
  fi
done

export PGPASSWORD="$DB_PASSWORD"
PSQL=(psql -X -q -v ON_ERROR_STOP=1 -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER")

EXISTS="$("${PSQL[@]}" -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname = '$TARGET'")"
if [[ "$EXISTS" == "1" ]]; then
  [[ "$FORCE" -eq 1 ]] || die "target database '$TARGET' already exists; pass --force to restore over it"
  echo "restoring over existing database $TARGET"
  CLEAN=(--clean --if-exists)
else
  "${PSQL[@]}" -d postgres -c "CREATE DATABASE \"$TARGET\""
  "${PSQL[@]}" -d "$TARGET" -c "CREATE EXTENSION IF NOT EXISTS vector; CREATE EXTENSION IF NOT EXISTS pg_trgm;"
  echo "created database $TARGET"
  CLEAN=()
fi

command -v pg_restore >/dev/null 2>&1 || die "pg_restore not found on PATH"
pg_restore --no-owner ${CLEAN[@]+"${CLEAN[@]}"} -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" \
  --dbname "$TARGET" "$DIR/db.dump"
echo "database restored into $TARGET"

if [[ "$FORCE" -eq 1 ]]; then
  if [[ -f "$DIR/storage.tar" ]]; then
    mkdir -p "$DATA_DIR"
    tar -xf "$DIR/storage.tar" -C "$DATA_DIR"
    echo "storage restored into $DATA_DIR"
  else
    echo "no storage.tar in $DIR; storage not restored"
  fi
else
  echo "storage.tar left in place at $DIR/storage.tar (pass --force to extract into $DATA_DIR)"
fi
