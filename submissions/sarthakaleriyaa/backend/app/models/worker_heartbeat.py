from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class WorkerHeartbeat(Base):
    """One row per worker process; /health uses it to tell if a worker is alive."""

    __tablename__ = "worker_heartbeats"

    worker_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    last_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
