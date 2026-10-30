import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.config import ai_provider, env_int
from app.core.database import get_db
from app.models.document import Document
from app.models.worker_heartbeat import WorkerHeartbeat

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Health"])


def _as_utc(value: datetime) -> datetime:
    # SQLite returns naive datetimes; Postgres returns aware ones.
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _check(name: str, fn) -> dict:
    try:
        return fn()
    except Exception as exc:
        logger.warning(
            "Health check failed",
            extra={"check": name, "error_type": type(exc).__name__},
        )
        return {"status": "error", "detail": "unreachable"}


@router.get("/health/live", summary="Liveness: the API process is running")
def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get(
    "/health",
    summary="Readiness: database, vector store, worker and queue",
    responses={503: {"description": "One or more dependencies are unhealthy"}},
)
def health(db: Session = Depends(get_db)):
    now = datetime.now(timezone.utc)

    def database():
        db.execute(text("SELECT 1"))
        return {"status": "ok"}

    def vector_store():
        if db.get_bind().dialect.name != "postgresql":
            return {"status": "error", "detail": "pgvector requires PostgreSQL"}
        version = db.scalar(
            text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
        )
        if version is None:
            return {"status": "error", "detail": "pgvector extension missing"}
        return {"status": "ok", "pgvector": version}

    def worker():
        stale_after = env_int("WORKER_HEARTBEAT_STALE_SECONDS", 60)
        last_seen = db.scalar(select(func.max(WorkerHeartbeat.last_seen)))
        if last_seen is None:
            return {"status": "error", "detail": "no worker has reported"}
        age = (now - _as_utc(last_seen)).total_seconds()
        status = "ok" if age <= stale_after else "error"
        return {"status": status, "last_seen_seconds_ago": round(age, 1)}

    def queue():
        queued, oldest = db.execute(
            select(func.count(Document.id), func.min(Document.created_at)).where(
                Document.status == "queued"
            )
        ).one()
        result = {"status": "ok", "queued": queued}
        if oldest is not None:
            result["oldest_queued_seconds"] = round(
                (now - _as_utc(oldest)).total_seconds(), 1
            )
        return result

    checks = {"database": _check("database", database)}

    if checks["database"]["status"] == "ok":
        checks["vector_store"] = _check("vector_store", vector_store)
        checks["worker"] = _check("worker", worker)
        checks["queue"] = _check("queue", queue)
    else:
        db.rollback()
        for name in ("vector_store", "worker", "queue"):
            checks[name] = {"status": "error", "detail": "database unreachable"}

    healthy = all(check["status"] == "ok" for check in checks.values())

    return JSONResponse(
        status_code=200 if healthy else 503,
        content={
            "status": "ok" if healthy else "degraded",
            "ai_provider": ai_provider(),
            "checks": checks,
        },
    )
