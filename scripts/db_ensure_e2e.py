"""Create (or reset) the e2e database.

A fresh database per run is deliberate: an e2e suite that inherits state from a
previous run cannot fail for a reason you can read. `DROP ... WITH (FORCE)` terminates
any connection still attached, which is what makes this work even if a previous run's
api is still shutting down.
"""

from __future__ import annotations

import argparse
import sys

import psycopg


def main() -> int:
    parser = argparse.ArgumentParser(description="prepare the e2e database")
    parser.add_argument("--db-url", required=True, help="target database URL")
    parser.add_argument(
        "--keep",
        action="store_true",
        help="create if absent but do not wipe an existing database",
    )
    args = parser.parse_args()

    from psycopg.conninfo import conninfo_to_dict

    info = conninfo_to_dict(args.db_url.replace("postgresql+psycopg://", "postgresql://", 1))
    target = info.get("dbname")
    if not target:
        print("could not read a database name from the URL", file=sys.stderr)
        return 2

    admin = dict(info)
    admin["dbname"] = "postgres"

    with psycopg.connect(**admin, autocommit=True) as conn, conn.cursor() as cur:
        if args.keep:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (target,))
            if cur.fetchone():
                print(f"db_ensure_e2e: keeping the existing {target}")
                return 0

        print(f"db_ensure_e2e: resetting {target}")
        cur.execute(f'DROP DATABASE IF EXISTS "{target}" WITH (FORCE)')
        cur.execute(f'CREATE DATABASE "{target}"')
        for extension in ("vector", "pg_trgm"):
            cur.execute(f'CREATE EXTENSION IF NOT EXISTS "{extension}"')
    print(f"db_ensure_e2e: {target} is ready")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
