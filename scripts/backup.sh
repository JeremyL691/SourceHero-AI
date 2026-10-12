#!/usr/bin/env bash
# Back up the SourceHero database (pg_dump -Fc) and the local storage directory.
#
# Usage: scripts/backup.sh
# Reads: DATABASE_URL (environment, else .env), DATA_DIR (environment, else .env, else ./data)
# Env:   BACKUP_ROOT (default <repo>/backups)
# Output: <BACKUP_ROOT>/<UTC timestamp>/{db.dump,storage.tar,MANIFEST.txt}; prints that path.
# Secrets are never printed: the password is handed to the client through PGPASSWORD only.
set -euo pipefail

die() { echo "backup.sh: $*" >&2; exit 1; }

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# Parse DATABASE_URL and DATA_DIR into shell assignments (password included, never printed).
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


raw = value("DATABASE_URL")
if not raw:
    sys.exit("DATABASE_URL is not set (environment or .env)")
for prefix in ("postgres://", "postgresql://"):
    if raw.startswith(prefix):
        raw = "postgresql+psycopg://" + raw[len(prefix):]
url = make_url(raw)
print(f"DB_HOST={shlex.quote(url.host or '127.0.0.1')}")
print(f"DB_PORT={shlex.quote(str(url.port or 5432))}")
print(f"DB_USER={shlex.quote(url.username or '')}")
print(f"DB_NAME={shlex.quote(url.database or '')}")
print(f"DB_PASSWORD={shlex.quote(url.password or '')}")
print(f"DATA_DIR_RAW={shlex.quote(value('DATA_DIR') or './data')}")
PY
)" || die "could not read database settings"
eval "$CONF"

DATA_DIR="$DATA_DIR_RAW"
[[ "$DATA_DIR" == /* ]] || DATA_DIR="$REPO_ROOT/$DATA_DIR"

BACKUP_ROOT="${BACKUP_ROOT:-$REPO_ROOT/backups}"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="$BACKUP_ROOT/$TS"
[[ ! -e "$OUT" ]] || die "backup directory already exists: $OUT"
mkdir -p "$OUT"
trap 'echo "backup.sh: failed, removing $OUT" >&2; rm -rf "$OUT"' ERR

# 1. Database.
if command -v pg_dump >/dev/null 2>&1; then
  PGPASSWORD="$DB_PASSWORD" pg_dump -Fc -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -f "$OUT/db.dump"
else
  CONTAINER="$(docker ps --filter status=running --format '{{.Names}}	{{.Image}}' 2>/dev/null \
    | awk -F'\t' '$2 ~ /postgres/ {print $1; exit}')" || CONTAINER=""
  [[ -n "$CONTAINER" ]] || die "no pg_dump on PATH and no running postgres docker container"
  PGPASSWORD="$DB_PASSWORD" docker exec -e PGPASSWORD -i "$CONTAINER" \
    pg_dump -Fc -h localhost -U "$DB_USER" -d "$DB_NAME" > "$OUT/db.dump"
fi

# 2. Storage (empty archive when the directory does not exist yet).
if [[ -d "$DATA_DIR" ]]; then
  tar -cf "$OUT/storage.tar" -C "$DATA_DIR" .
else
  tar -cf "$OUT/storage.tar" -T /dev/null
fi

# 3. Manifest: timestamp, database name and checksums only.
if command -v sha256sum >/dev/null 2>&1; then SHA=(sha256sum); else SHA=(shasum -a 256); fi
{
  echo "timestamp: $TS"
  echo "database: $DB_NAME"
  (cd "$OUT" && "${SHA[@]}" db.dump storage.tar)
} > "$OUT/MANIFEST.txt"

trap - ERR
echo "$OUT"
