"""Export FastAPI OpenAPI schema to a JSON file."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.main import create_app  # noqa: E402


def main() -> None:
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("dashboard-web/openapi.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    app = create_app()
    schema = app.openapi()
    target.write_text(json.dumps(schema, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Exported OpenAPI schema to {target}")


if __name__ == "__main__":
    main()
