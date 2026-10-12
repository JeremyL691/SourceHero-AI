"""Ask the live demo three questions and keep what each stream did (plan K3.6).

Runs in-process against the database named by DATABASE_URL, through FastAPI's TestClient,
so no server or port is needed. Writes .dev/logs/live-demo.json for the report.
"""

from __future__ import annotations

import json
from pathlib import Path

QUESTIONS = [
    "What is the energy density of the HX-2?",
    "How many tokens was Orchid-7 trained on?",
    "What launch price did the HX-2 pack have?",
]
OUT = Path(".dev/logs/live-demo.json")
DEMO_EMAIL = "demo@sourcehero.local"


def parse_sse(body: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    for block in body.strip().split("\n\n"):
        name, data = None, None
        for line in block.splitlines():
            if line.startswith("event: "):
                name = line[len("event: ") :]
            elif line.startswith("data: "):
                data = json.loads(line[len("data: ") :])
        if name is not None and data is not None:
            events.append((name, data))
    return events


def main() -> int:
    from fastapi.testclient import TestClient
    from sqlalchemy import select

    from app.core.db import SessionLocal
    from app.core.security import create_access_token
    from app.main import create_app
    from app.models.user import User

    db = SessionLocal()
    try:
        user_id = db.execute(select(User.id).where(User.email == DEMO_EMAIL)).scalar_one()
    finally:
        db.close()

    results = []
    with TestClient(create_app()) as client:
        client.headers.update({"Authorization": f"Bearer {create_access_token(user_id)}"})
        for question in QUESTIONS:
            with client.stream("POST", "/v1/ask", json={"question": question}) as response:
                status = response.status_code
                events = parse_sse(response.read().decode())
            by_name = dict(events)
            results.append(
                {
                    "question": question,
                    "status": status,
                    "events": [name for name, _ in events],
                    "retrieval_hits": len(by_name.get("retrieval", {}).get("hits", [])),
                    "deltas": sum(1 for name, _ in events if name == "delta"),
                    "sentences": [
                        {"text": data["text"], "verdict": data["verdict"]}
                        for name, data in events
                        if name == "sentence"
                    ],
                    "errors": [data for name, data in events if name == "error"],
                    "method": by_name.get("done", {}).get("method"),
                    "answer_id": by_name.get("done", {}).get("answer_id"),
                }
            )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    for row in results:
        print(
            f"{row['status']} {row['method']} hits={row['retrieval_hits']} deltas={row['deltas']} :: {row['question']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
