"""End-to-end tests against real PostgreSQL + pgvector.

They run the Alembic migration, the upload API, the worker and the real
cosine-distance SQL. The LLM and embeddings use the offline `local` provider
or a scripted fake, so no API keys or network calls are needed.

Skipped unless TEST_DATABASE_URL points at a disposable database, e.g.
    TEST_DATABASE_URL=postgresql+psycopg://test:test@127.0.0.1:56543/test
WARNING: every table in that database is dropped and recreated.
"""

import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import sessionmaker

from app import worker
from app.core.database import get_db
from app.main import app
from app.models.document_chunk import DocumentChunk
from app.services import document_processor, rag
from app.services.local_provider import LocalClient
from tests.helpers import auth, make_pdf, signup_and_login

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL, reason="TEST_DATABASE_URL is not set"
)

BACKEND_DIR = Path(__file__).resolve().parents[1]

HANDBOOK = (
    "Employee Handbook\n\n"
    "Annual leave: full-time employees receive 25 days of paid annual leave per "
    "calendar year. Unused leave of up to 5 days may be carried over.\n\n"
    "Remote work: employees may work remotely up to three days per week with "
    "manager approval.\n"
).encode()

SECURITY_PDF = make_pdf(
    [
        "IT Security Policy overview.",
        "Passwords must be at least 14 characters long and rotated every 180 days.",
    ]
)


@pytest.fixture(scope="module")
def pg_engine():
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    config.set_main_option("sqlalchemy.url", TEST_DATABASE_URL.replace("%", "%%"))

    engine = create_engine(TEST_DATABASE_URL)
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))

    command.upgrade(config, "head")
    yield engine
    engine.dispose()


@pytest.fixture()
def pg(pg_engine, monkeypatch):
    with pg_engine.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE users, documents, document_chunks, question_history, "
                "worker_heartbeats RESTART IDENTITY CASCADE"
            )
        )

    factory = sessionmaker(bind=pg_engine, autoflush=False, autocommit=False)

    def override_get_db():
        session = factory()
        try:
            yield session
        finally:
            session.close()

    monkeypatch.setenv("AI_PROVIDER", "local")
    monkeypatch.setattr(document_processor, "SessionLocal", factory)
    monkeypatch.setattr(worker, "SessionLocal", factory)
    app.dependency_overrides[get_db] = override_get_db
    yield factory
    app.dependency_overrides.clear()


@pytest.fixture()
def api(pg):
    from fastapi.testclient import TestClient

    with TestClient(app) as test_client:
        yield test_client


def upload_and_process(api, token, name, content, content_type="text/plain"):
    response = api.post(
        "/documents", files={"file": (name, content, content_type)}, headers=auth(token)
    )
    assert response.status_code == 202
    assert response.json()["status"] == "queued"

    while worker.run_once() is not None:
        pass

    document = api.get(f"/documents/{response.json()['id']}", headers=auth(token)).json()
    assert document["status"] == "ready", document
    return document


def ask(api, token, question, **extra):
    return api.post("/chat", json={"question": question, **extra}, headers=auth(token))


def test_migrations_reach_head_with_pgvector_and_health_is_ok(api, pg):
    with pg() as db:
        version = db.scalar(text("SELECT version_num FROM alembic_version"))
        column = db.scalar(
            text(
                "SELECT format_type(atttypid, atttypmod) FROM pg_attribute "
                "WHERE attrelid = 'document_chunks'::regclass AND attname = 'embedding'"
            )
        )
        hnsw = db.scalar(
            text("SELECT 1 FROM pg_indexes WHERE indexname = 'ix_document_chunks_embedding_hnsw'")
        )
        worker.record_heartbeat(db, "integration-worker")

    assert version == "0004"
    assert column == "vector(768)"
    # Dropped in 0003: retrieval ranks a user's chunks exactly instead.
    assert hnsw is None

    response = api.get("/health")
    assert response.status_code == 200, response.json()
    assert response.json()["checks"]["vector_store"]["status"] == "ok"


def test_full_flow_answers_with_citations_and_page_numbers(api):
    token = signup_and_login(api, "flow@example.com")
    handbook = upload_and_process(api, token, "handbook.md", HANDBOOK)
    pdf = upload_and_process(
        api, token, "security.pdf", SECURITY_PDF, "application/pdf"
    )
    assert pdf["page_count"] == 2

    leave = ask(api, token, "How many days of paid annual leave do employees receive?")
    body = leave.json()
    assert leave.status_code == 200
    assert body["refused"] is False
    assert "25 days" in body["answer"]
    assert body["citations"][0]["document_id"] == handbook["id"]
    assert body["citations"][0]["filename"] == "handbook.md"
    assert body["usage"]["latency_ms"] >= 0

    passwords = ask(api, token, "How long must passwords be?").json()
    assert "14 characters" in passwords["answer"]
    assert passwords["citations"][0]["filename"] == "security.pdf"
    assert passwords["citations"][0]["page_number"] == 2

    history = api.get("/chat/history", headers=auth(token)).json()
    assert [h["status"] for h in history] == ["answered", "answered"]


def test_unanswerable_question_is_refused(api):
    token = signup_and_login(api, "refuse@example.com")
    upload_and_process(api, token, "handbook.md", HANDBOOK)

    body = ask(api, token, "What is the company's stock ticker symbol?").json()

    assert body["refused"] is True
    assert body["answer"] == "I couldn't find this in your documents."
    assert body["citations"] == []


def test_users_never_retrieve_each_others_chunks(api):
    alice = signup_and_login(api, "alice@example.com")
    bob = signup_and_login(api, "bob@example.com")
    alice_doc = upload_and_process(api, alice, "handbook.md", HANDBOOK)

    # Bob has no documents: the same question must not reach Alice's chunks.
    bob_answer = ask(api, bob, "How many days of paid annual leave do employees receive?")
    assert bob_answer.json()["refused"] is True
    assert bob_answer.json()["citations"] == []

    # Naming Alice's document ID explicitly is rejected, not answered.
    forced = ask(api, bob, "annual leave?", document_ids=[alice_doc["id"]])
    assert forced.status_code == 404

    # With his own similar document, Bob only ever sees his own citations.
    bob_doc = upload_and_process(
        api,
        bob,
        "bob-handbook.md",
        b"Annual leave: contractors receive 10 days of paid annual leave per year.",
    )
    body = ask(api, bob, "How many days of paid annual leave are there?").json()
    assert {c["document_id"] for c in body["citations"]} == {bob_doc["id"]}

    assert api.get(f"/documents/{alice_doc['id']}", headers=auth(bob)).status_code == 404
    assert api.delete(f"/documents/{alice_doc['id']}", headers=auth(bob)).status_code == 404


def test_selected_documents_restrict_retrieval(api):
    token = signup_and_login(api, "select@example.com")
    handbook = upload_and_process(api, token, "handbook.md", HANDBOOK)
    pdf = upload_and_process(api, token, "security.pdf", SECURITY_PDF, "application/pdf")

    only_pdf = ask(
        api,
        token,
        "How many days of paid annual leave do employees receive?",
        document_ids=[pdf["id"]],
    ).json()
    only_handbook = ask(
        api,
        token,
        "How many days of paid annual leave do employees receive?",
        document_ids=[handbook["id"]],
    ).json()

    assert only_pdf["refused"] is True
    assert only_handbook["citations"][0]["document_id"] == handbook["id"]


def test_deleted_document_is_never_cited_again(api, pg):
    token = signup_and_login(api, "delete@example.com")
    document = upload_and_process(api, token, "handbook.md", HANDBOOK)
    question = "How many days of paid annual leave do employees receive?"

    assert ask(api, token, question).json()["refused"] is False

    assert api.delete(f"/documents/{document['id']}", headers=auth(token)).status_code == 204

    with pg() as db:
        remaining = db.scalar(select(func.count(DocumentChunk.id)))
    assert remaining == 0

    after = ask(api, token, question).json()
    assert after["refused"] is True
    assert after["citations"] == []


def test_mocked_llm_receives_grounded_prompt_and_failures_return_503(api, monkeypatch):
    token = signup_and_login(api, "mock@example.com")
    upload_and_process(api, token, "handbook.md", HANDBOOK)
    sent = {}

    class MockLLM(LocalClient):
        def __init__(self, fail=False):
            super().__init__()
            embed = self.models.embed_content

            def generate_content(*, model, contents, config):
                if fail:
                    raise rag.httpx.ReadTimeout("timed out")
                sent["prompt"] = contents
                sent["system"] = config.system_instruction
                return SimpleNamespace(
                    text="Employees receive 25 days [S1].",
                    usage_metadata=SimpleNamespace(
                        prompt_token_count=321, candidates_token_count=12
                    ),
                )

            self.models = SimpleNamespace(
                embed_content=embed, generate_content=generate_content
            )

    monkeypatch.setattr(rag, "get_ai_client", lambda: MockLLM())
    body = ask(api, token, "How much annual leave is there?").json()

    assert body["answer"] == "Employees receive 25 days [S1]."
    assert body["usage"]["input_tokens"] == 321
    assert body["citations"][0]["source_id"] == "S1"
    assert "25 days of paid annual leave" in sent["prompt"]
    assert sent["system"] == rag.SYSTEM_INSTRUCTION

    monkeypatch.setattr(rag, "get_ai_client", lambda: MockLLM(fail=True))
    failed = ask(api, token, "How much annual leave is there?")

    assert failed.status_code == 503
    assert "temporarily unavailable" in failed.json()["detail"]
    assert api.get("/chat/history", headers=auth(token)).json()[0]["status"] == "error"


def test_hnsw_retrieval_finds_a_users_chunks_behind_many_closer_foreign_chunks(pg):
    """Regression: an HNSW scan returns ~ef_search (40) global neighbours and
    filters by owner afterwards, so a user with no chunks in that set used to
    get zero results. Iterative index scans must keep searching."""
    from app.models.document import Document
    from app.models.user import User

    dims = 768

    def vector(main: float, other_axis: int, other: float) -> list[float]:
        v = [0.0] * dims
        v[0], v[other_axis] = main, other
        return v

    with pg() as db:
        crowd, owner = User(email="crowd@example.com", hashed_password="x"), User(
            email="owner@example.com", hashed_password="x"
        )
        db.add_all([crowd, owner])
        db.flush()
        crowd_doc = Document(user_id=crowd.id, filename="c.txt", stored_path="c", content_type="text/plain", size_bytes=1, status="ready")
        owner_doc = Document(user_id=owner.id, filename="o.txt", stored_path="o", content_type="text/plain", size_bytes=1, status="ready")
        db.add_all([crowd_doc, owner_doc])
        db.flush()
        for i in range(200):
            # Very close to the query (distance ~0.005).
            db.add(DocumentChunk(document_id=crowd_doc.id, chunk_index=i, content="crowd", embedding=vector(1.0, 1 + i, 0.1)))
        # Further away (distance 0.5), but the only chunk this user owns.
        db.add(DocumentChunk(document_id=owner_doc.id, chunk_index=0, content="mine", embedding=vector(0.5, 300, 0.866)))
        db.commit()

        # Recreate the index and force the planner towards it, as it would
        # choose for one very large tenant. Before the fix this returned [].
        db.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_test_hnsw ON document_chunks "
                "USING hnsw (embedding vector_cosine_ops)"
            )
        )
        db.execute(text("SET LOCAL enable_seqscan = off"))
        db.execute(text("SET LOCAL enable_sort = off"))
        rows = rag.retrieve_chunks(db, owner.id, vector(1.0, 1, 0.0), None, 5)
        contents = [chunk.content for chunk, _, _ in rows]
        db.rollback()  # drops the test index again

    assert contents == ["mine"]


def test_hotel_limit_answered_with_citation_and_ceo_refused(api):
    """The two questions of the controlled Gemini check, on the offline provider."""
    token = signup_and_login(api, "hotel@example.com")
    path = BACKEND_DIR.parent / "evaluation" / "documents" / "kestrel_expense_policy.txt"
    document = upload_and_process(api, token, "kestrel_expense_policy.txt", path.read_bytes())

    hotel = ask(api, token, "What is the hotel reimbursement limit per night?").json()
    assert hotel["refused"] is False
    assert "160 EUR" in hotel["answer"]
    assert hotel["citations"][0]["document_id"] == document["id"]
    assert "220 EUR per night in London" in hotel["citations"][0]["passage"]

    unrelated = ask(api, token, "What is the company's stock ticker symbol?").json()
    assert unrelated["refused"] is True
    assert unrelated["citations"] == []


@pytest.mark.parametrize("provider_fails", [False, True])
def test_slow_llm_call_does_not_hold_a_database_transaction(api, pg_engine, monkeypatch, provider_fails):
    """Regression (found in the live Gemini check): /chat kept the retrieval
    transaction open during the model call. A slow call outlasted Postgres's
    idle_in_transaction_session_timeout, the connection was killed, and the
    request failed as "database unavailable" instead of answering (or reporting
    the provider error)."""
    import time

    from google.genai import errors

    token = signup_and_login(api, f"slow-{provider_fails}@example.com")
    upload_and_process(api, token, "handbook.md", HANDBOOK)

    strict = create_engine(
        TEST_DATABASE_URL,
        connect_args={"options": "-c idle_in_transaction_session_timeout=1000"},
    )
    factory = sessionmaker(bind=strict, autoflush=False, autocommit=False)

    def strict_db():
        session = factory()
        try:
            yield session
        finally:
            session.close()

    class SlowLLM(LocalClient):
        def __init__(self):
            super().__init__()
            embed = self.models.embed_content

            def generate_content(*, model, contents, config):
                time.sleep(1.6)  # longer than the 1 s idle-transaction limit
                if provider_fails:
                    raise errors.ServerError(504, {"error": {"status": "DEADLINE_EXCEEDED"}})
                return SimpleNamespace(
                    text="Employees receive 25 days [S1].",
                    usage_metadata=SimpleNamespace(prompt_token_count=10, candidates_token_count=5),
                )

            self.models = SimpleNamespace(embed_content=embed, generate_content=generate_content)

    app.dependency_overrides[get_db] = strict_db
    monkeypatch.setattr(rag, "get_ai_client", lambda: SlowLLM())
    try:
        response = ask(api, token, "How many days of paid annual leave do employees receive?")
    finally:
        strict.dispose()

    if provider_fails:
        assert response.status_code == 503
        assert response.json()["detail"].startswith("The AI service is temporarily unavailable")
    else:
        assert response.status_code == 200, response.json()
        assert response.json()["answer"] == "Employees receive 25 days [S1]."
