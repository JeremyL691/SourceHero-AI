"""stdio transport: `python -m app.mcp`.

The token comes from `SOURCEHERO_TOKEN` and the database URL from `DATABASE_URL`, both
supplied by whoever launches the process (see docs/AGENTS_API.md).

**Everything on stdout is the protocol.** Logs go to stderr; a stray print would
corrupt the stream and the client would fail to parse the session.
"""

from __future__ import annotations

import os
import sys

from app.core.logging import configure_logging


def _fail(message: str) -> int:
    print(message, file=sys.stderr)
    return 2


def main() -> int:
    configure_logging()

    token = os.environ.get("SOURCEHERO_TOKEN", "").strip()
    if not token:
        return _fail("SOURCEHERO_TOKEN is not set; create a token in Settings -> Tokens & MCP")

    database_url = os.environ.get("DATABASE_URL", "").strip()
    if database_url:
        # Applied before any app import so the engine binds to the caller's database.
        os.environ["DATABASE_URL"] = database_url

    from app.mcp.server import _resolve_pat_user, build_mcp, current_user

    user = _resolve_pat_user(token)
    if user is None:
        return _fail("the token in SOURCEHERO_TOKEN is invalid, revoked, or unknown")

    token_value = current_user.set(user)
    server = build_mcp()
    print(f"SourceHero MCP ready for {user.email}", file=sys.stderr)
    try:
        server.run()
    finally:
        current_user.reset(token_value)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
