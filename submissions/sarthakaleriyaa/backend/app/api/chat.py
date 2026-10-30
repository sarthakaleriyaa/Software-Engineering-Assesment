
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.config import env_int
from app.core.database import get_db
from app.models.question import QuestionHistory
from app.models.user import User
from app.schemas.chat import ChatRequest, ChatResponse, QuestionHistoryItem
from app.services.rag import (
    AIProviderRateLimited,
    AIProviderUnavailable,
    DocumentSelectionError,
    answer_question,
)
from app.services.redaction import redact_sensitive

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["Chat"])

RATE_LIMIT_WINDOW_SECONDS = 60


def _enforce_rate_limit(db: Session, user_id: int) -> None:
    """Per-user sliding window, counted from question history in the database.

    Stored in Postgres rather than process memory, so the limit holds across
    API replicas and restarts. Every attempt is counted, including failures.
    """
    limit = env_int("RATE_LIMIT_PER_MINUTE", 10)
    window_start = datetime.now(timezone.utc) - timedelta(
        seconds=RATE_LIMIT_WINDOW_SECONDS
    )

    recent = db.scalar(
        select(func.count(QuestionHistory.id)).where(
            QuestionHistory.user_id == user_id,
            QuestionHistory.created_at >= window_start,
        )
    )

    if recent >= limit:
        logger.info("Rate limit exceeded user_id=%s", user_id)
        raise HTTPException(
            status_code=429,
            detail=(
                f"Rate limit exceeded: at most {limit} questions per minute. "
                "Please wait and try again."
            ),
            headers={"Retry-After": str(RATE_LIMIT_WINDOW_SECONDS)},
        )


def _finish(db: Session, record: QuestionHistory, **fields) -> None:
    for key, value in fields.items():
        setattr(record, key, value)
    db.commit()


def _mark_failed(db: Session, record: QuestionHistory) -> None:
    """Best-effort history update on an error path.

    If the database itself fails here, log it and carry on, so the caller still
    reports the original (provider or selection) error rather than masking it
    as a database error.
    """
    try:
        _finish(db, record, status="error")
    except SQLAlchemyError as exc:
        db.rollback()
        logger.error(
            "Could not record failed question",
            extra={"question_id": record.id, "error_type": type(exc).__name__},
        )


@router.post(
    "",
    response_model=ChatResponse,
    summary="Ask a question across all ready documents or selected ones",
    responses={
        404: {"description": "A selected document is missing or not ready"},
        429: {"description": "Per-user rate limit exceeded"},
        503: {"description": "AI provider unavailable or its usage limit reached"},
    },
)
def chat(
    payload: ChatRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _enforce_rate_limit(db, current_user.id)

    record = QuestionHistory(
        user_id=current_user.id,
        question=redact_sensitive(payload.question),
        document_ids=payload.document_ids,
        status="pending",
        created_at=datetime.now(timezone.utc),
    )
    db.add(record)
    db.commit()
    db.refresh(record)

    try:
        result = answer_question(
            db=db,
            user_id=current_user.id,
            question=payload.question,
            document_ids=payload.document_ids,
        )

    except DocumentSelectionError as exc:
        _mark_failed(db, record)
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        ) from exc

    except AIProviderRateLimited as exc:
        logger.warning("AI provider rate limited user_id=%s", current_user.id)
        _mark_failed(db, record)

        raise HTTPException(
            status_code=503,
            detail=(
                "The AI service has reached its usage limit. "
                "Please try again later."
            ),
            headers={"Retry-After": "60"},
        ) from exc

    except AIProviderUnavailable as exc:
        logger.warning(
            "AI provider unavailable user_id=%s",
            current_user.id,
        )
        _mark_failed(db, record)

        raise HTTPException(
            status_code=503,
            detail=(
                "The AI service is temporarily unavailable. "
                "Please try again shortly."
            ),
        ) from exc

    except Exception:
        logger.exception(
            "Chat request failed for user_id=%s",
            current_user.id,
        )
        db.rollback()
        _mark_failed(db, record)

        raise HTTPException(
            status_code=500,
            detail="An unexpected error occurred while processing your question.",
        ) from None

    answer = redact_sensitive(result["answer"])
    citations = result.get("citations", [])

    for citation in citations:
        citation["filename"] = redact_sensitive(citation["filename"])
        citation["passage"] = redact_sensitive(citation["passage"])

    usage = {
        "input_tokens": result.get("input_tokens"),
        "output_tokens": result.get("output_tokens"),
        "latency_ms": result.get("latency_ms"),
        "estimated_cost_usd": result.get("estimated_cost_usd"),
    }
    refused = bool(result.get("refused", False))

    _finish(
        db,
        record,
        answer=answer,
        status="refused" if refused else "answered",
        citations=citations,
        **usage,
    )

    logger.info(
        "Question answered",
        extra={"user_id": current_user.id, "refused": refused, **usage},
    )

    return {
        "question_id": record.id,
        "answer": answer,
        "refused": refused,
        "citations": citations,
        "usage": usage,
    }


@router.get(
    "/history",
    response_model=list[QuestionHistoryItem],
    summary="The current user's past questions, newest first",
)
def history(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    rows = db.scalars(
        select(QuestionHistory)
        .where(QuestionHistory.user_id == current_user.id)
        .order_by(QuestionHistory.created_at.desc(), QuestionHistory.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()

    return [
        {
            "id": row.id,
            "question": row.question,
            "answer": row.answer,
            "status": row.status,
            "document_ids": row.document_ids,
            "citations": row.citations,
            "usage": {
                "input_tokens": row.input_tokens,
                "output_tokens": row.output_tokens,
                "latency_ms": row.latency_ms,
                "estimated_cost_usd": row.estimated_cost_usd,
            },
            "created_at": row.created_at,
        }
        for row in rows
    ]
