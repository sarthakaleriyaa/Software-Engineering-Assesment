
import io
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PdfReadError
from sqlalchemy import delete

from app.core.config import env_int
from app.core.database import SessionLocal
from app.models.document import Document
from app.models.document_blob import DocumentBlob
from app.models.document_chunk import DocumentChunk
from app.services.rag import (
    AIProviderRateLimited,
    AIProviderUnavailable,
    embed_texts,
    get_ai_client,
)

logger = logging.getLogger(__name__)

EMBEDDING_BATCH_SIZE = 50
MAX_CHUNKS_PER_DOCUMENT = 2000
MAX_ATTEMPTS = 3


class DocumentProcessingError(Exception):
    """A failure whose message is safe to show to the document owner."""


def retry_delay_seconds(attempts: int) -> int:
    """Exponential backoff for provider outages: base, 2x base, ... capped at 5 minutes."""
    base = env_int("WORKER_RETRY_BASE_SECONDS", 30, 0)
    return min(base * 2 ** max(attempts - 1, 0), 300)


def chunk_settings() -> tuple[int, int]:
    return env_int("CHUNK_SIZE", 1000, 100), env_int("CHUNK_OVERLAP", 150, 0)


def extract_pages(path: str) -> list[tuple[int | None, str]]:
    """Return (page_number, text) pairs. Text files have no page numbers."""
    file_path = Path(path)
    return extract_pages_from_bytes(file_path.read_bytes(), file_path.suffix)


def extract_pages_from_bytes(data: bytes, extension: str) -> list[tuple[int | None, str]]:
    extension = extension.lower()

    if extension in {".txt", ".md"}:
        pages = [(None, data.decode("utf-8", errors="replace"))]

    elif extension == ".pdf":
        reader = PdfReader(io.BytesIO(data))
        pages = [
            (number, page.extract_text() or "")
            for number, page in enumerate(reader.pages, start=1)
        ]

    else:
        raise ValueError("Unsupported document type")

    pages = [(number, text.strip()) for number, text in pages if text.strip()]
    if not pages:
        raise ValueError("No extractable text found in document")

    return pages


def extract_text(path: str) -> str:
    return "\n".join(text for _, text in extract_pages(path))


def chunk_text(text: str, chunk_size: int = 1000, overlap: int = 150) -> list[str]:
    """Fixed-size character windows with overlap, cut at whitespace when possible."""
    if chunk_size <= 0 or overlap < 0 or overlap >= chunk_size:
        raise ValueError("Invalid chunk size or overlap")

    chunks = []
    start = 0

    while start < len(text):
        end = min(start + chunk_size, len(text))

        if end < len(text):
            # Prefer a cut at whitespace in the last 20% of the window,
            # so words are not split in half.
            boundary = text.rfind(" ", start + int(chunk_size * 0.8), end)
            newline = text.rfind("\n", start + int(chunk_size * 0.8), end)
            boundary = max(boundary, newline)
            if boundary > start + overlap:
                end = boundary

        chunk = text[start:end].strip()

        if chunk:
            chunks.append(chunk)

        if end == len(text):
            break

        next_start = end - overlap
        # Start the overlap on a word boundary too, if one is close by.
        if next_start > 0 and not text[next_start - 1].isspace():
            space = text.find(" ", next_start, end)
            if space != -1:
                next_start = space + 1
        start = next_start

    return chunks


def chunk_pages(
    pages: list[tuple[int | None, str]],
    chunk_size: int,
    overlap: int,
) -> list[tuple[int | None, str]]:
    # Chunks never cross a page boundary, so each citation has one page number.
    return [
        (number, chunk)
        for number, text in pages
        for chunk in chunk_text(text, chunk_size, overlap)
    ]


def _safe_reason(exc: Exception) -> str:
    if isinstance(exc, DocumentProcessingError):
        return str(exc)
    if isinstance(exc, PdfReadError):
        return "The PDF file could not be read"
    if isinstance(exc, ValueError) and "extractable text" in str(exc):
        return "No extractable text found (scanned PDFs are not supported)"
    if isinstance(exc, ValueError) and "Unsupported" in str(exc):
        return "Unsupported document type"
    if isinstance(exc, FileNotFoundError):
        return "The uploaded file is missing from storage"
    if isinstance(exc, AIProviderRateLimited):
        return "The AI provider's usage limit was reached; try uploading again later"
    if isinstance(exc, AIProviderUnavailable):
        return "The embedding service was unavailable"
    return "Document processing failed"


def process_document(document_id: int) -> str | None:
    """Extract, chunk, embed and store one document. Returns the final status.

    Never raises: failures are recorded on the document so the worker keeps running.
    Embedding-provider outages are retried by re-queuing (up to MAX_ATTEMPTS).
    """
    db = SessionLocal()

    try:
        document = db.get(Document, document_id)
        if document is None:
            return None

        if document.status != "processing":
            document.status = "processing"
            document.attempts = (document.attempts or 0) + 1
            db.commit()

        blob = db.get(DocumentBlob, document_id)
        if blob is not None:
            pages = extract_pages_from_bytes(blob.data, Path(document.filename).suffix)
        else:
            # Uploaded before file bytes moved into the database.
            pages = extract_pages(document.stored_path)
        chunk_size, overlap = chunk_settings()
        chunks = chunk_pages(pages, chunk_size, overlap)

        if len(chunks) > MAX_CHUNKS_PER_DOCUMENT:
            raise DocumentProcessingError(
                "Document is too large to index"
            )

        client = get_ai_client()

        # Embed chunks in batches to reduce API calls.
        vectors: list[list[float]] = []
        for start in range(0, len(chunks), EMBEDDING_BATCH_SIZE):
            batch = [text for _, text in chunks[start : start + EMBEDDING_BATCH_SIZE]]
            vectors.extend(embed_texts(client, batch, "RETRIEVAL_DOCUMENT"))
            # Progress marker: stale-job recovery only re-queues documents
            # whose updated_at stops moving, not ones that are just slow.
            document.updated_at = datetime.now(timezone.utc)
            db.commit()

        # Replace existing chunks if the document is reprocessed.
        db.execute(
            delete(DocumentChunk).where(
                DocumentChunk.document_id == document_id
            )
        )

        for index, ((page_number, content), vector) in enumerate(
            zip(chunks, vectors, strict=True)
        ):
            db.add(
                DocumentChunk(
                    document_id=document_id,
                    chunk_index=index,
                    content=content,
                    page_number=page_number,
                    embedding=vector,
                )
            )

        document.status = "ready"
        document.failure_reason = None
        document.page_count = sum(1 for number, _ in pages if number is not None) or None
        document.chunk_count = len(chunks)
        db.commit()

        logger.info(
            "Document processed",
            extra={"document_id": document_id, "chunks": len(chunks)},
        )
        return "ready"

    except Exception as exc:
        db.rollback()
        reason = _safe_reason(exc)
        status = "failed"

        try:
            document = db.get(Document, document_id)
            if document is not None:
                retryable = isinstance(exc, AIProviderUnavailable)
                if retryable and (document.attempts or 0) < MAX_ATTEMPTS:
                    status = "queued"
                    document.failure_reason = None
                    document.available_at = datetime.now(timezone.utc) + timedelta(
                        seconds=retry_delay_seconds(document.attempts or 1)
                    )
                else:
                    document.failure_reason = reason
                document.status = status
                db.commit()
            else:
                status = None
        except Exception:
            db.rollback()

        logger.warning(
            "Document processing failed",
            extra={
                "document_id": document_id,
                "error_type": type(exc).__name__,
                "next_status": status,
            },
        )
        return status

    finally:
        db.close()
