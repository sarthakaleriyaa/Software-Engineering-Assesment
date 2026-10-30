
from pathlib import Path
from uuid import uuid4

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    UploadFile,
    status,
)
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core import config
from app.core.database import get_db
from app.core.logging import request_id_var
from app.models.document import Document
from app.models.document_blob import DB_STORAGE_PREFIX, DocumentBlob
from app.models.document_chunk import DocumentChunk
from app.models.question import QuestionHistory
from app.models.user import User
from app.schemas.document import DocumentResponse

router = APIRouter(prefix="/documents", tags=["Documents"])

MAX_FILE_SIZE = config.env_int("MAX_UPLOAD_MB", 10) * 1024 * 1024

ALLOWED_EXTENSIONS = {".pdf", ".txt", ".md"}

DELETED_PASSAGE = "[Passage removed: the document was deleted]"


def _get_owned_document(db: Session, document_id: int, user_id: int) -> Document:
    # Ownership is part of the lookup, so another user's ID behaves like a missing one.
    document = db.scalar(
        select(Document).where(
            Document.id == document_id,
            Document.user_id == user_id,
        )
    )

    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found",
        )

    return document


def _scrub_history_passages(db: Session, user_id: int, document_id: int) -> None:
    """Remove copies of a deleted document's text from saved answers.

    History keeps the answer and which file was cited, but not the passage,
    so deleted content is never shown again.
    """
    rows = db.scalars(
        select(QuestionHistory).where(
            QuestionHistory.user_id == user_id,
            QuestionHistory.citations.is_not(None),
        )
    )
    for row in rows:
        if any(c.get("document_id") == document_id for c in row.citations):
            row.citations = [
                {**c, "passage": DELETED_PASSAGE} if c.get("document_id") == document_id else c
                for c in row.citations
            ]


def _looks_like_declared_type(extension: str, content: bytes) -> bool:
    if extension == ".pdf":
        return content.startswith(b"%PDF-")
    # Text files must decode as UTF-8 and contain no NUL bytes.
    if b"\x00" in content[:8192]:
        return False
    try:
        content[:8192].decode("utf-8")
    except UnicodeDecodeError as exc:
        # A multi-byte character may be cut at the 8 KB boundary.
        return exc.start >= 8188
    return True


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=DocumentResponse,
    summary="Upload a document (PDF, TXT, MD); processing happens in the worker",
)
async def upload_document(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    filename = Path(file.filename or "").name
    extension = Path(filename).suffix.lower()

    if not filename or extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only PDF, TXT, and Markdown files are supported",
        )

    content = await file.read(MAX_FILE_SIZE + 1)

    if not content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File is empty",
        )

    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"File size must not exceed {MAX_FILE_SIZE // (1024 * 1024)} MB",
        )

    if not _looks_like_declared_type(extension, content):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File content does not match its extension",
        )

    stored_name = f"{uuid4().hex}{extension}"

    try:
        # Committing a `queued` row is the enqueue: the worker polls for it.
        # The file bytes go into the same transaction (document_blobs), so a
        # queued document always has its file, on any host.
        document = Document(
            user_id=current_user.id,
            filename=filename[:255],
            stored_path=f"{DB_STORAGE_PREFIX}{stored_name}",
            content_type=(file.content_type or "application/octet-stream")[:100],
            size_bytes=len(content),
            status="queued",
            request_id=request_id_var.get(),
        )

        db.add(document)
        db.flush()
        db.add(DocumentBlob(document_id=document.id, data=content))
        db.commit()
        db.refresh(document)

    except Exception:
        db.rollback()

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not save document",
        ) from None

    finally:
        await file.close()

    return document


@router.get("", response_model=list[DocumentResponse])
def list_documents(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return db.scalars(
        select(Document)
        .where(Document.user_id == current_user.id)
        .order_by(Document.created_at.desc(), Document.id.desc())
    ).all()


@router.get("/{document_id}", response_model=DocumentResponse)
def get_document(
    document_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return _get_owned_document(db, document_id, current_user.id)


@router.delete(
    "/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a document, its chunks and embeddings, and its passages in saved answers",
)
def delete_document(
    document_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    document = _get_owned_document(db, document_id, current_user.id)
    on_disk = not document.stored_path.startswith(DB_STORAGE_PREFIX)
    path = Path(document.stored_path)

    try:
        # Explicit, rather than relying only on ON DELETE CASCADE, so chunk
        # removal does not depend on database foreign-key settings.
        db.execute(
            delete(DocumentChunk).where(DocumentChunk.document_id == document.id)
        )
        db.execute(delete(DocumentBlob).where(DocumentBlob.document_id == document.id))
        _scrub_history_passages(db, current_user.id, document.id)
        db.delete(document)
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not delete document",
        ) from None

    # Remove the local file after the database transaction succeeds.
    if on_disk:
        path.unlink(missing_ok=True)
