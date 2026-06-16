"""v0.4.1 U1 — /demo/seed-and-ingest runs ingestion as part of the seed call."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.database import Base, engine
from app.ingestion.base import ExtractedDocument
from app.main import app

pytestmark = pytest.mark.skip(reason="Binds to the global Postgres engine; needs pgvector test infra to run locally.")


@pytest.fixture(autouse=True)
def _clean_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


@pytest.fixture
def client(monkeypatch):
    """Patch ingesters to return one synthetic document each so the test is offline."""
    def fake_doc(source_type: str, name: str) -> ExtractedDocument:
        return ExtractedDocument(
            title=f"{name} sample",
            raw_text="Demo content paragraph one. Demo content paragraph two.",
            clean_text="Demo content paragraph one. Demo content paragraph two.",
            url=f"https://example.com/{source_type}",
            metadata={"page": 1},
        )

    monkeypatch.setattr("app.services.pipeline.ingest_webpage",
                        lambda url: [fake_doc("webpage", url)])
    monkeypatch.setattr("app.services.pipeline.ingest_rss",
                        lambda url, fetch_full_articles=True: [fake_doc("rss", url)])
    return TestClient(app)


def test_seed_and_ingest_creates_chunks(client):
    response = client.post("/demo/seed-and-ingest")
    assert response.status_code == 200
    payload = response.json()
    assert "ingestion" in payload
    assert len(payload["ingestion"]) == 3  # 3 demo sources
    # At least one source must have ingested with chunks
    assert payload["total_chunks_inserted"] > 0
    # All ingestion entries should have a status field
    for entry in payload["ingestion"]:
        assert entry["status"] in {"success", "failed"}
