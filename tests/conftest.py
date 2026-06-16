import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth.dependencies import User, get_current_user
from app.database import Base, get_db
from app.main import app


TEST_USER_ID = "00000000-0000-0000-0000-000000000001"


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, future=True)
    with Session() as db:
        yield db


@pytest.fixture
def user_id():
    return TEST_USER_ID


@pytest.fixture
def make_user_id():
    """Returns a function that yields a fresh UUID string per call."""
    def _make() -> str:
        return str(uuid.uuid4())
    return _make


@pytest.fixture
def auth_client():
    """FastAPI TestClient with SQLite DB override and a fake authenticated user."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, future=True)

    def override_db():
        with Session() as db:
            yield db

    fake_user = User(user_id=TEST_USER_ID, email="test@example.com")

    async def override_user():
        return fake_user

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    try:
        yield TestClient(app), Session
    finally:
        app.dependency_overrides.clear()
