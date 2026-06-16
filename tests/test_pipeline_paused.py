"""v0.4.1 B1 — ingest must refuse paused sources and not silently unpause them."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from tests.conftest import TEST_USER_ID
from app.database import Base
from app.models import Source
from app.services.pipeline import SourcePausedError, ingest_source


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, future=True)
    with Session() as db:
        yield db


def test_paused_source_rejects_ingest(db_session):
    src = Source(user_id=TEST_USER_ID, source_type="webpage", name="paused thing", url="https://example.com", status="paused")
    db_session.add(src)
    db_session.commit()

    with pytest.raises(SourcePausedError):
        ingest_source(db_session, TEST_USER_ID, src.id)

    # Status stays paused; no IngestionRun row should have been written for a successful run.
    db_session.refresh(src)
    assert src.status == "paused", "ingest_source should not change status of a paused source"


def test_failed_ingest_preserves_paused_status_set_concurrently(db_session, monkeypatch):
    """If a source is somehow paused mid-flight, a failure must not flip it to 'failed'."""
    src = Source(user_id=TEST_USER_ID, source_type="webpage", name="will fail", url="http://invalid.example.invalid", status="active")
    db_session.add(src)
    db_session.commit()

    # Simulate a concurrent pause: flip status right before the ingest helper runs.
    def failing_webpage(url):
        # Concurrently flip status — emulating an external API call pausing the source.
        s = db_session.get(Source, src.id)
        s.status = "paused"
        db_session.commit()
        raise RuntimeError("simulated ingest failure")

    monkeypatch.setattr("app.services.pipeline.ingest_webpage", failing_webpage)
    run = ingest_source(db_session, TEST_USER_ID, src.id)
    assert run.status == "failed"
    db_session.refresh(src)
    assert src.status == "paused", "must not overwrite paused → failed"
