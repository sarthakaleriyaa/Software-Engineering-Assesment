
from sqlalchemy import func, select

from app.api import documents as documents_api
from app.models.document import Document
from app.models.document_blob import DocumentBlob
from app.models.document_chunk import DocumentChunk
from tests.helpers import auth, make_pdf, signup_and_login


def upload(client, token, name, content, content_type="text/plain"):
    return client.post(
        "/documents",
        files={"file": (name, content, content_type)},
        headers=auth(token),
    )


def test_upload_returns_202_queued_without_processing_inline(client, db_session):
    token = signup_and_login(client, "up@example.com")

    response = upload(client, token, "notes.md", b"# Notes\nHello world")

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "queued"
    assert body["filename"] == "notes.md"

    # The API only enqueues; no chunks exist until the worker runs.
    document = db_session.get(Document, body["id"])
    assert document.status == "queued"
    # The file bytes are stored with the row, so any host (and the worker) can read them.
    blob = db_session.get(DocumentBlob, document.id)
    assert document.stored_path.startswith("db:")
    assert blob is not None and blob.data.startswith(b"# Notes") and blob.data.endswith(b"Hello world")
    assert db_session.scalar(select(func.count(DocumentChunk.id))) == 0


def test_upload_accepts_pdf(client):
    token = signup_and_login(client, "pdf@example.com")

    response = upload(
        client, token, "guide.pdf", make_pdf(["Page one"]), "application/pdf"
    )

    assert response.status_code == 202


def test_upload_rejects_unsupported_extension(client):
    token = signup_and_login(client, "ext@example.com")

    response = upload(client, token, "script.exe", b"MZ...")

    assert response.status_code == 400


def test_upload_rejects_content_that_does_not_match_extension(client):
    token = signup_and_login(client, "fake@example.com")

    fake_pdf = upload(client, token, "fake.pdf", b"not really a pdf")
    binary_txt = upload(client, token, "fake.txt", b"\x00\x01\x02binary")

    assert fake_pdf.status_code == 400
    assert binary_txt.status_code == 400


def test_upload_rejects_empty_and_oversized_files(client, monkeypatch):
    token = signup_and_login(client, "size@example.com")
    monkeypatch.setattr(documents_api, "MAX_FILE_SIZE", 100)

    empty = upload(client, token, "empty.txt", b"")
    too_big = upload(client, token, "big.txt", b"a" * 101)

    assert empty.status_code == 400
    assert too_big.status_code == 413


def test_upload_requires_authentication(client):
    response = client.post(
        "/documents", files={"file": ("a.txt", b"hello", "text/plain")}
    )

    assert response.status_code == 401


def test_get_document_status_is_owner_only(client):
    owner = signup_and_login(client, "owner@example.com")
    other = signup_and_login(client, "other@example.com")
    document_id = upload(client, owner, "a.txt", b"hello there").json()["id"]

    own = client.get(f"/documents/{document_id}", headers=auth(owner))
    foreign = client.get(f"/documents/{document_id}", headers=auth(other))
    missing = client.get("/documents/999999", headers=auth(owner))

    assert own.status_code == 200
    assert own.json()["status"] == "queued"
    # Same response as a missing document, so IDs cannot be probed.
    assert foreign.status_code == 404
    assert foreign.json() == missing.json()


def test_delete_removes_chunks_embeddings_and_file(client, db_session):
    token = signup_and_login(client, "del@example.com")
    body = upload(client, token, "a.txt", b"hello there").json()
    document_id = body["id"]
    assert db_session.get(DocumentBlob, document_id) is not None

    for index in range(3):
        db_session.add(
            DocumentChunk(
                document_id=document_id,
                chunk_index=index,
                content=f"chunk {index}",
                embedding=[0.1] * 768,
            )
        )
    db_session.commit()

    response = client.delete(f"/documents/{document_id}", headers=auth(token))

    assert response.status_code == 204
    db_session.expire_all()
    assert db_session.get(Document, document_id) is None
    remaining = db_session.scalar(
        select(func.count(DocumentChunk.id)).where(
            DocumentChunk.document_id == document_id
        )
    )
    assert remaining == 0
    assert db_session.get(DocumentBlob, document_id) is None


def test_delete_of_other_users_document_keeps_its_chunks(client, db_session):
    owner = signup_and_login(client, "keep@example.com")
    attacker = signup_and_login(client, "attacker@example.com")
    document_id = upload(client, owner, "a.txt", b"hello there").json()["id"]
    db_session.add(
        DocumentChunk(document_id=document_id, chunk_index=0, content="secret")
    )
    db_session.commit()

    response = client.delete(f"/documents/{document_id}", headers=auth(attacker))

    assert response.status_code == 404
    assert db_session.scalar(select(func.count(DocumentChunk.id))) == 1


def test_delete_scrubs_document_passages_from_history(client, db_session):
    from app.api.documents import DELETED_PASSAGE
    from app.models.question import QuestionHistory

    token = signup_and_login(client, "scrub@example.com")
    gone = upload(client, token, "gone.txt", b"secret plan").json()["id"]
    kept = upload(client, token, "kept.txt", b"public plan").json()["id"]
    user_id = db_session.get(Document, gone).user_id
    db_session.add(
        QuestionHistory(
            user_id=user_id,
            question="plans?",
            answer="Two plans [S1][S2].",
            status="answered",
            citations=[
                {"document_id": gone, "filename": "gone.txt", "chunk_index": 0, "passage": "secret plan"},
                {"document_id": kept, "filename": "kept.txt", "chunk_index": 0, "passage": "public plan"},
            ],
        )
    )
    db_session.commit()

    assert client.delete(f"/documents/{gone}", headers=auth(token)).status_code == 204

    citations = client.get("/chat/history", headers=auth(token)).json()[0]["citations"]
    assert [c["passage"] for c in citations] == [DELETED_PASSAGE, "public plan"]
    assert "secret plan" not in str(citations)


def test_legacy_on_disk_document_is_still_deleted_with_its_file(client, db_session, tmp_path):
    """Documents uploaded before file bytes moved into Postgres keep working."""
    token = signup_and_login(client, "legacy@example.com")
    user_id = client.get("/auth/me", headers=auth(token)).json()["id"]
    legacy = tmp_path / "legacy.txt"
    legacy.write_text("legacy content", encoding="utf-8")
    document = Document(
        user_id=user_id, filename="legacy.txt", stored_path=str(legacy),
        content_type="text/plain", size_bytes=14, status="ready",
    )
    db_session.add(document)
    db_session.commit()

    assert client.delete(f"/documents/{document.id}", headers=auth(token)).status_code == 204
    assert not legacy.exists()
