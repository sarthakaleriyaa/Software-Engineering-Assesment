import os
import tempfile

# Tests must never read a developer's real .env (or its API keys).
os.environ["DOCUMIND_DISABLE_DOTENV"] = "1"
os.environ["AI_PROVIDER"] = "gemini"
os.environ.pop("GEMINI_API_KEY", None)
os.environ["UPLOAD_DIR"] = tempfile.mkdtemp(prefix="documind-test-uploads-")

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://test:test@127.0.0.1:5432/test",
)
os.environ.setdefault(
    "JWT_SECRET_KEY",
    "test-only-secret-not-for-production",
)
os.environ.setdefault("JWT_ALGORITHM", "HS256")
os.environ.setdefault("ACCESS_TOKEN_EXPIRE_MINUTES", "30")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  (registers every table)
from app.core.database import Base, get_db
from app.main import app


@pytest.fixture()
def db_engine():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    # SQLite stands in for Postgres here; pgvector distance queries are
    # covered by the Postgres-backed tests in test_postgres_integration.py.
    Base.metadata.create_all(bind=engine)

    yield engine

    engine.dispose()


@pytest.fixture()
def session_factory(db_engine):
    return sessionmaker(
        bind=db_engine,
        autoflush=False,
        autocommit=False,
    )


@pytest.fixture()
def db_session(session_factory):
    session = session_factory()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def client(session_factory):
    def override_get_db():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()
