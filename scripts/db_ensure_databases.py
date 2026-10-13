"""Create the auxiliary databases this project uses (test / eval / e2e) and make sure
the `vector` and `pg_trgm` extensions exist in each of them.

`infra/db/init/01-init.sql` only runs on the *first* initialization of the Docker
volume, so `db-reset` or a wiped volume would otherwise be missing them.
"""

from __future__ import annotations

import sys

from psycopg import connect, sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from app.core.config import get_settings
from app.core.db import psycopg_url

MAIN_DB = "sourcehero"
AUX_DB = ("sourcehero_test", "sourcehero_eval", "sourcehero_e2e")
EXTENSIONS = ("vector", "pg_trgm")


def _url_for(base: str, dbname: str) -> str:
    """Same URL, different database: replace only the path component."""
    info = conninfo_to_dict(psycopg_url(base))
    info["dbname"] = dbname
    return make_conninfo(**info)


def main() -> int:
    settings = get_settings()
    admin = psycopg_url(settings.database_url)

    with connect(admin, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("SELECT datname FROM pg_database")
        existing = {row[0] for row in cur.fetchall()}
        created = []
        for name in AUX_DB:
            if name not in existing:
                cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
                created.append(name)

    for name in (MAIN_DB, *AUX_DB):
        try:
            with connect(_url_for(admin, name), autocommit=True) as conn, conn.cursor() as cur:
                for ext in EXTENSIONS:
                    cur.execute(sql.SQL("CREATE EXTENSION IF NOT EXISTS {}").format(sql.Identifier(ext)))
        except Exception as exc:  # noqa: BLE001 - report and keep going
            print(f"db_ensure_databases: could not prepare {name}: {exc}", file=sys.stderr)
            return 1

    print(
        "db_ensure_databases: "
        + (f"created {', '.join(created)}; " if created else "")
        + f"{MAIN_DB}, {', '.join(AUX_DB)} ready with {', '.join(EXTENSIONS)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
