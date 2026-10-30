import pytest
from sqlalchemy import select

from app import worker
from app.models.document import Document
from app.models.document_chunk import DocumentChunk
from app.models.user import User
from app.services import document_processor, rag
from app.services.document_processor import (
    MAX_ATTEMPTS,
    chunk_pages,
    chunk_text,
    extract_pages,
    process_document,
)
from app.services.rag import AIProviderUnavailable
from tests.helpers import make_pdf


@pytest.fixture()
def local_ai(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "local")


@pytest.fixture()
def bound_sessions(monkeypatch, session_factory):
    # Point the worker and processor at the in-memory test database.
    monkeypatch.setattr(document_processor, "SessionLocal", session_factory)
    monkeypatch.setattr(worker, "SessionLocal", session_factory)
    return session_factory


def make_document(db, tmp_path, name: str, content: bytes) -> Document:
    user = db.scalar(select(User).where(User.email == "w@example.com"))
    if user is None:
        user = User(email="w@example.com", hashed_password="x")
        db.add(user)
        db.commit()

    path = tmp_path / name
    path.write_bytes(content)
    document = Document(
        user_id=user.id,
        filename=name,
        stored_path=str(path),
        content_type="text/plain",
        size_bytes=len(content),
        status="queued",
    )
    db.add(document)
    db.commit()
    return document


def test_pdf_pages_are_extracted_with_page_numbers(tmp_path):
    path = tmp_path / "doc.pdf"
    path.write_bytes(make_pdf(["Alpha page text", "", "Gamma page text"]))

    pages = extract_pages(str(path))

    # Blank pages are dropped but numbering follows the original PDF.
    assert pages == [(1, "Alpha page text"), (3, "Gamma page text")]


def test_chunks_never_cross_pages():
    pages = [(1, "one " * 300), (2, "two " * 300)]

    chunks = chunk_pages(pages, chunk_size=500, overlap=50)

    for page, text in chunks:
        assert ("one" in text) != ("two" in text)
        assert page == (1 if "one" in text else 2)


def test_chunking_prefers_word_boundaries():
    text = " ".join(f"word{i}" for i in range(400))

    chunks = chunk_text(text, chunk_size=200, overlap=30)

    for chunk in chunks:
        for token in chunk.split():
            assert token.startswith("word") and token[4:].isdigit()


def test_process_document_stores_chunks_and_marks_ready(
    db_session, bound_sessions, local_ai, tmp_path
):
    document = make_document(
        db_session,
        tmp_path,
        "guide.pdf",
        make_pdf(["Refunds are processed in 5 days.", "Support is open 9 to 5."]),
    )

    status = process_document(document.id)

    db_session.expire_all()
    document = db_session.get(Document, document.id)
    chunks = db_session.scalars(
        select(DocumentChunk).where(DocumentChunk.document_id == document.id)
    ).all()
    assert status == "ready"
    assert document.status == "ready"
    assert document.failure_reason is None
    assert document.page_count == 2
    assert document.chunk_count == 2
    assert [c.page_number for c in chunks] == [1, 2]
    assert all(c.embedding is not None and len(c.embedding) == 768 for c in chunks)


def test_unreadable_document_fails_with_safe_reason(
    db_session, bound_sessions, local_ai, tmp_path
):
    document = make_document(db_session, tmp_path, "blank.txt", b"   \n  ")

    status = process_document(document.id)

    db_session.expire_all()
    document = db_session.get(Document, document.id)
    assert status == "failed"
    assert document.status == "failed"
    assert document.failure_reason.startswith("No extractable text")


def test_corrupt_pdf_fails_without_crashing(
    db_session, bound_sessions, local_ai, tmp_path
):
    document = make_document(db_session, tmp_path, "bad.pdf", b"%PDF-1.4 garbage")

    assert process_document(document.id) == "failed"

    db_session.expire_all()
    reason = db_session.get(Document, document.id).failure_reason
    assert reason in {
        "The PDF file could not be read",
        "No extractable text found (scanned PDFs are not supported)",
        "Document processing failed",
    }


def test_provider_outage_is_retried_then_fails(
    db_session, bound_sessions, monkeypatch, tmp_path
):
    def broken_client():
        raise AIProviderUnavailable("down")

    monkeypatch.setattr(document_processor, "get_ai_client", broken_client)
    # No backoff here, so every retry is immediately claimable.
    monkeypatch.setenv("WORKER_RETRY_BASE_SECONDS", "0")
    document = make_document(db_session, tmp_path, "a.txt", b"Some text here.")

    statuses = []
    for _ in range(MAX_ATTEMPTS):
        assert worker.run_once() == document.id
        db_session.expire_all()
        statuses.append(db_session.get(Document, document.id).status)

    assert statuses == ["queued"] * (MAX_ATTEMPTS - 1) + ["failed"]
    final = db_session.get(Document, document.id)
    assert final.attempts == MAX_ATTEMPTS
    assert final.failure_reason == "The embedding service was unavailable"
    assert worker.run_once() is None


def test_provider_timeout_during_embedding_is_unavailable(monkeypatch):
    import httpx

    class FakeModels:
        def embed_content(self, **kwargs):
            raise httpx.ConnectTimeout("timed out")

    class FakeClient:
        models = FakeModels()

    with pytest.raises(AIProviderUnavailable):
        rag.embed_texts(FakeClient(), ["text"], "RETRIEVAL_DOCUMENT")


def test_worker_claims_oldest_queued_document_and_records_heartbeat(
    db_session, bound_sessions, local_ai, tmp_path
):
    from app.models.worker_heartbeat import WorkerHeartbeat

    first = make_document(db_session, tmp_path, "1.txt", b"First document text.")
    second = make_document(db_session, tmp_path, "2.txt", b"Second document text.")

    assert worker.run_once() == first.id
    assert worker.run_once() == second.id
    assert worker.run_once() is None

    worker.record_heartbeat(db_session, "test-worker")
    assert db_session.get(WorkerHeartbeat, "test-worker") is not None


def test_stale_processing_documents_are_requeued(
    db_session, bound_sessions, tmp_path
):
    document = make_document(db_session, tmp_path, "s.txt", b"Stale text.")
    document.status = "processing"
    document.attempts = 1
    db_session.commit()

    # A negative threshold makes every processing row count as stale.
    assert worker.recover_stale_documents(db_session, -60) == 1

    db_session.expire_all()
    assert db_session.get(Document, document.id).status == "queued"


def test_retry_delay_grows_exponentially_and_is_capped(monkeypatch):
    monkeypatch.delenv("WORKER_RETRY_BASE_SECONDS", raising=False)

    delays = [document_processor.retry_delay_seconds(n) for n in (1, 2, 3, 10)]

    assert delays == [30, 60, 120, 300]


def test_provider_outage_waits_for_backoff_before_retrying(
    db_session, bound_sessions, monkeypatch, tmp_path
):
    from datetime import datetime, timedelta, timezone

    def broken_client():
        raise AIProviderUnavailable("down")

    monkeypatch.setattr(document_processor, "get_ai_client", broken_client)
    monkeypatch.delenv("WORKER_RETRY_BASE_SECONDS", raising=False)
    document = make_document(db_session, tmp_path, "b.txt", b"Some text here.")

    assert worker.run_once() == document.id

    db_session.expire_all()
    queued = db_session.get(Document, document.id)
    assert queued.status == "queued"
    available_at = queued.available_at.replace(tzinfo=queued.available_at.tzinfo or timezone.utc)
    wait = available_at - datetime.now(timezone.utc)
    assert timedelta(seconds=20) < wait <= timedelta(seconds=30)
    # Not claimable until the backoff has passed.
    assert worker.run_once() is None

    queued.available_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db_session.commit()
    assert worker.run_once() == document.id


def test_heartbeat_thread_records_db_row_and_touches_file(
    db_session, bound_sessions, monkeypatch, tmp_path
):
    import threading

    from app.models.worker_heartbeat import WorkerHeartbeat

    heartbeat_file = tmp_path / "heartbeat"
    stop = threading.Event()
    monkeypatch.setattr(worker, "HEARTBEAT_FILE", heartbeat_file)
    monkeypatch.setattr(worker, "_stop_event", stop)

    thread = threading.Thread(target=worker._heartbeat_loop, args=(1,))
    thread.start()
    for _ in range(50):
        if heartbeat_file.exists():
            break
        threading.Event().wait(0.05)
    stop.set()
    thread.join(timeout=5)

    assert heartbeat_file.exists()
    assert db_session.get(WorkerHeartbeat, worker.WORKER_ID) is not None


def test_document_stored_in_database_is_processed_from_its_blob(
    db_session, bound_sessions, local_ai, tmp_path
):
    from app.models.document_blob import DocumentBlob

    user = User(email="blob@example.com", hashed_password="x")
    db_session.add(user)
    db_session.commit()
    document = Document(
        user_id=user.id, filename="guide.pdf", stored_path="db:abc.pdf",
        content_type="application/pdf", size_bytes=1, status="queued",
    )
    db_session.add(document)
    db_session.flush()
    db_session.add(DocumentBlob(document_id=document.id, data=make_pdf(["Blob page one.", "Blob page two."])))
    db_session.commit()

    assert worker.run_once() == document.id

    db_session.expire_all()
    processed = db_session.get(Document, document.id)
    pages = sorted(c.page_number for c in db_session.scalars(select(DocumentChunk).where(DocumentChunk.document_id == document.id)))
    assert processed.status == "ready"
    assert processed.page_count == 2
    assert pages == [1, 2]
