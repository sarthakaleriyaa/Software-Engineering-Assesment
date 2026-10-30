from sqlalchemy import ForeignKey, LargeBinary
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

# stored_path value for documents whose bytes live in document_blobs.
DB_STORAGE_PREFIX = "db:"


class DocumentBlob(Base):
    """The uploaded file itself, kept in Postgres.

    Hosted platforms give each service its own (often ephemeral) disk, so a
    file on the API's disk is neither durable nor readable by a separate
    worker. Storing the bytes with the document row fixes both, and they are
    written in the same transaction that enqueues the document.
    """

    __tablename__ = "document_blobs"

    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True
    )
    data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
