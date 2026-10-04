"""Entry point: `python -m app.serve [--reload]`.

Binds to 127.0.0.1 on the port from settings, never a hardcoded one.
"""

from __future__ import annotations

import argparse

import uvicorn

from app.core.config import get_settings


def main() -> None:
    parser = argparse.ArgumentParser(description="run the SourceHero API")
    parser.add_argument("--reload", action="store_true")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()

    settings = get_settings()
    port = args.port or settings.api_port
    uvicorn.run("app.main:app", host=args.host, port=port, reload=args.reload)


if __name__ == "__main__":
    main()