"""Ingestion worker: `python -m app.worker`.

The queue is the `documents` table itself. A worker claims the oldest
`queued` row with SELECT ... FOR UPDATE SKIP LOCKED, so several workers can
run in parallel without taking the same document, and a job can never be
lost between "row committed" and "message enqueued" (there is no separate
broker to fall out of sync with).
"""

import logging
import os
import signal
import socket
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.config import env_int, validate_ai_settings
from app.core.database import SessionLocal
from app.core.logging import configure_logging, request_id_var
from app.models.document import Document
from app.models.worker_heartbeat import WorkerHeartbeat
from app.services.document_processor import MAX_ATTEMPTS, process_document

logger = logging.getLogger("app.worker")

WORKER_ID = f"{socket.gethostname()}-{os.getpid()}"
# Touched after every successful heartbeat; the Compose healthcheck reads its age.
HEARTBEAT_FILE = Path(os.getenv("WORKER_HEARTBEAT_FILE", "/tmp/documind-worker-heartbeat"))

_stop_event = threading.Event()


def claim_next_document(db: Session) -> tuple[int, str | None] | None:
    document = db.scalars(
        select(Document)
        .where(
            Document.status == "queued",
            or_(
                Document.available_at.is_(None),
                Document.available_at <= datetime.now(timezone.utc),
            ),
        )
        .order_by(Document.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    ).first()

    if document is None:
        db.rollback()
        return None

    document.status = "processing"
    document.attempts = (document.attempts or 0) + 1
    document.available_at = None
    document.updated_at = datetime.now(timezone.utc)
    db.commit()
    return document.id, document.request_id


def recover_stale_documents(db: Session, stale_after_seconds: int) -> int:
    """Re-queue documents left in `processing` by a worker that died."""
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=stale_after_seconds)
    stale = db.scalars(
        select(Document)
        .where(Document.status == "processing", Document.updated_at < cutoff)
        .with_for_update(skip_locked=True)
    ).all()

    for document in stale:
        if (document.attempts or 0) >= MAX_ATTEMPTS:
            document.status = "failed"
            document.failure_reason = "Processing timed out"
        else:
            document.status = "queued"

    db.commit()
    return len(stale)


def record_heartbeat(db: Session, worker_id: str = WORKER_ID) -> None:
    heartbeat = db.get(WorkerHeartbeat, worker_id)
    now = datetime.now(timezone.utc)
    if heartbeat is None:
        db.add(WorkerHeartbeat(worker_id=worker_id, last_seen=now))
    else:
        heartbeat.last_seen = now
    db.commit()


def run_once() -> int | None:
    """Claim and process a single document. Returns its id, or None if idle."""
    db = SessionLocal()
    try:
        claimed = claim_next_document(db)
    finally:
        db.close()

    if claimed is None:
        return None

    document_id, request_id = claimed
    token = request_id_var.set(request_id)
    try:
        logger.info("Processing document", extra={"document_id": document_id})
        process_document(document_id)
    finally:
        request_id_var.reset(token)

    return document_id


def _stop(signum, frame) -> None:
    _stop_event.set()
    logger.info("Shutdown requested; finishing current document")


def _heartbeat_loop(interval_seconds: int) -> None:
    """Runs in its own thread so the heartbeat stays fresh during long documents."""
    while not _stop_event.is_set():
        db = SessionLocal()
        try:
            record_heartbeat(db)
            HEARTBEAT_FILE.touch()
        except Exception as exc:
            # No DB heartbeat and no file touch: /health and the container
            # healthcheck both report the worker as unhealthy.
            logger.error(
                "Heartbeat failed", extra={"error_type": type(exc).__name__}
            )
        finally:
            db.close()
        _stop_event.wait(interval_seconds)


def run_loop() -> None:
    """Claim and process documents until stop() is called."""
    poll_seconds = env_int("WORKER_POLL_SECONDS", 2)
    stale_seconds = env_int("WORKER_STALE_SECONDS", 600)
    heartbeat_seconds = env_int("WORKER_HEARTBEAT_SECONDS", 10)

    logger.info("Worker started", extra={"worker_id": WORKER_ID})
    threading.Thread(
        target=_heartbeat_loop, args=(heartbeat_seconds,), daemon=True
    ).start()
    last_recovery = 0.0

    while not _stop_event.is_set():
        try:
            now = time.monotonic()
            if now - last_recovery >= 60:
                db = SessionLocal()
                try:
                    recovered = recover_stale_documents(db, stale_seconds)
                finally:
                    db.close()
                if recovered:
                    logger.warning(
                        "Re-queued stale documents", extra={"count": recovered}
                    )
                last_recovery = now

            if run_once() is None:
                _stop_event.wait(poll_seconds)

        except Exception as exc:
            # Database blips must not kill the worker; back off and retry.
            logger.error(
                "Worker loop error",
                extra={"error_type": type(exc).__name__},
            )
            _stop_event.wait(min(poll_seconds * 5, 30))

    logger.info("Worker stopped", extra={"worker_id": WORKER_ID})


def run_forever() -> None:
    """Standalone worker process (`python -m app.worker`)."""
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    run_loop()


def start_embedded() -> threading.Thread:
    """Run the worker inside the API process (RUN_EMBEDDED_WORKER=true).

    For single-service hosts without background workers (e.g. free tiers).
    The queue, retries and heartbeats are the same as in the separate worker.
    """
    _stop_event.clear()
    thread = threading.Thread(target=run_loop, name="documind-worker", daemon=True)
    thread.start()
    return thread


def stop() -> None:
    _stop_event.set()


if __name__ == "__main__":
    configure_logging()
    validate_ai_settings()
    run_forever()
