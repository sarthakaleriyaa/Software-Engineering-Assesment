
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

DOCUMENT_STATUSES = ("queued", "processing", "ready", "failed")


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    stored_path: Mapped[str] = mapped_column(String(500), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), default="queued", nullable=False, index=True
    )
    # Safe, user-facing reason only; never raw exception text.
    failure_reason: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    attempts: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    # Earliest time the worker may (re)try this document; null means now.
    available_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    chunk_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Request ID of the upload, so worker logs can be joined to the API request.
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
