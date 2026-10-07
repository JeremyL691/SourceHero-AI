"""Export FastAPI OpenAPI schema to a JSON file."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from app.main import create_app


def main() -> None:
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("dashboard-web/openapi.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    app = create_app()
    schema = app.openapi()
    target.write_text(json.dumps(schema, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Exported OpenAPI schema to {target}")


if __name__ == "__main__":
    main()
