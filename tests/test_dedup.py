from tests.conftest import TEST_USER_ID
from app.database import Base
from app.ingestion.base import ExtractedDocument
from app.ingestion.quality import store_extracted_documents
from app.models import Source
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def test_store_extracted_documents_skips_duplicate_documents():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, future=True)
    with Session() as db:
        source = Source(user_id=TEST_USER_ID, source_type="webpage", name="Example", url="https://example.com")
        db.add(source)
        db.commit()
        db.refresh(source)
        doc = ExtractedDocument(title="Same", raw_text="alpha beta gamma", clean_text="alpha beta gamma", url="https://example.com/a")
        first = store_extracted_documents(db, source, [doc])
        second = store_extracted_documents(db, source, [doc])
        assert first["documents_inserted"] == 1
        assert first["chunks_inserted"] == 1
        assert second["duplicates_skipped"] == 1

