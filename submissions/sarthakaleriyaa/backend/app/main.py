import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import SQLAlchemyError

from app.api.auth import router as auth_router
from app.api.chat import router as chat_router
from app.api.documents import router as documents_router
from app.api.health import router as health_router
from app.core.config import validate_ai_settings
from app.core.logging import configure_logging, request_id_var, sanitize_request_id
from app.core.security import JWT_SECRET_KEY, MIN_SECRET_LENGTH, jwt_secret_is_weak

configure_logging()
logger = logging.getLogger(__name__)

if not JWT_SECRET_KEY:
    raise RuntimeError("JWT_SECRET_KEY is not configured")
validate_ai_settings()
if jwt_secret_is_weak(JWT_SECRET_KEY):
    logger.warning(
        "JWT_SECRET_KEY is shorter than %s characters; tokens are easier to forge",
        MIN_SECRET_LENGTH,
    )

STATIC_DIR = Path(__file__).resolve().parent / "static"
INDEX_HTML = (STATIC_DIR / "index.html").read_text(encoding="utf-8")


# The UI may load scripts and styles only from this origin (no inline code)
# and talk only to this API. /docs is excluded: Swagger UI loads from a CDN.
UI_CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self'; "
    "img-src 'self' data:; connect-src 'self'; "
    "base-uri 'none'; form-action 'self'; frame-ancestors 'none'; object-src 'none'"
)

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}

def embedded_worker_enabled() -> bool:
    return os.getenv("RUN_EMBEDDED_WORKER", "").strip().lower() in {"1", "true", "yes"}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if embedded_worker_enabled():
        from app import worker

        logger.info("Starting embedded ingestion worker")
        worker.start_embedded()
        try:
            yield
        finally:
            worker.stop()
    else:
        yield


app = FastAPI(
    lifespan=lifespan,
    title="DocuMind API",
    description=(
        "Upload PDF, TXT or Markdown documents and ask questions answered only "
        "from them, with citations. Authenticate with `POST /auth/login`, then "
        "use the returned token as a Bearer token (the Authorize button)."
    ),
    version="1.0.0",
)


@app.middleware("http")
async def request_context(request: Request, call_next):
    request_id = (
        sanitize_request_id(request.headers.get("X-Request-ID"))
        or uuid.uuid4().hex
    )
    token = request_id_var.set(request_id)
    started = time.perf_counter()
    status_code = 500

    try:
        response = await call_next(request)
        status_code = response.status_code
        response.headers["X-Request-ID"] = request_id
        response.headers.update(SECURITY_HEADERS)
        if request.url.path.startswith("/static/"):
            # Revalidate UI assets on every load so a deploy takes effect at once.
            response.headers["Cache-Control"] = "no-cache"
        return response
    finally:
        logger.info(
            "request",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status_code": status_code,
                "duration_ms": round((time.perf_counter() - started) * 1000, 1),
            },
        )
        request_id_var.reset(token)


@app.exception_handler(SQLAlchemyError)
async def database_exception_handler(
    request: Request, exc: SQLAlchemyError
) -> JSONResponse:
    logger.error(
        "Database operation failed path=%s type=%s",
        request.url.path,
        type(exc).__name__,
    )

    return JSONResponse(
        status_code=503,
        content={
            "detail": (
                "The database is temporarily unavailable. "
                "Please try again shortly."
            ),
            "request_id": request_id_var.get(),
        },
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    # Drop the echoed "input" so submitted passwords never come back in errors.
    errors = [
        {"loc": error.get("loc"), "msg": error.get("msg"), "type": error.get("type")}
        for error in exc.errors()
    ]
    return JSONResponse(status_code=422, content={"detail": errors})


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.error(
        "Unhandled error path=%s type=%s",
        request.url.path,
        type(exc).__name__,
    )
    return JSONResponse(
        status_code=500,
        content={
            "detail": "An unexpected error occurred.",
            "request_id": request_id_var.get(),
        },
    )


@app.get("/", include_in_schema=False)
def index() -> HTMLResponse:
    return HTMLResponse(
        INDEX_HTML,
        headers={"Content-Security-Policy": UI_CONTENT_SECURITY_POLICY},
    )


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

app.include_router(health_router)

app.include_router(auth_router)

app.include_router(documents_router)

app.include_router(chat_router)
