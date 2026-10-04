"""Block until Postgres is accepting connections on DATABASE_URL (up to --timeout seconds).

Never uses SQLAlchemy: psycopg connects directly so this works before the app's
models or engine exist.
"""

from __future__ import annotations

import argparse
import sys
import time

from psycopg import connect
from psycopg.conninfo import conninfo_to_dict

from app.core.db import normalize_db_url, psycopg_url


def main() -> int:
    parser = argparse.ArgumentParser(description="wait for the database")
    parser.add_argument("--timeout", type=float, default=90.0)
    parser.add_argument("--url", default=None, help="override DATABASE_URL")
    parser.add_argument(
        "--expect-db",
        default=None,
        help="database that must exist and be connectable (defaults to the URL's database)",
    )
    args = parser.parse_args()

    from app.core.config import get_settings

    url = args.url or get_settings().database_url
    url = normalize_db_url(url)
    try:
        info = conninfo_to_dict(url.replace("postgresql+psycopg://", "postgresql://", 1))
    except Exception:
        info = {}
    target_db = args.expect_db or info.get("dbname")

    deadline = time.monotonic() + args.timeout
    attempt = 0
    last_error: Exception | None = None

    while time.monotonic() < deadline:
        attempt += 1
        try:
            kwargs: dict[str, object] = {}
            if target_db and info.get("dbname") and target_db != info["dbname"]:
                kwargs["dbname"] = target_db
            with connect(psycopg_url(url), connect_timeout=3, **kwargs) as conn:  # type: ignore[arg-type]
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
                    cur.fetchone()
            print(f"wait_for_db: connected after {attempt} attempt(s)", file=sys.stderr)
            return 0
        except Exception as exc:  # noqa: BLE001 - report and retry until the deadline
            last_error = exc
            time.sleep(1.0)

    print(f"wait_for_db: timed out after {args.timeout}s: {last_error}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())