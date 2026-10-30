import json
import logging
import os
import re
import sys
from contextvars import ContextVar
from datetime import datetime, timezone

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

_STANDARD_ATTRS = set(
    logging.LogRecord("", 0, "", 0, "", None, None).__dict__
) | {"message", "asctime"}


def sanitize_request_id(value: str | None) -> str | None:
    """Accept a client-supplied request ID only if it is short and safe to log."""
    if value and _SAFE_REQUEST_ID.match(value):
        return value
    return None


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": request_id_var.get(),
        }

        # Fields passed with `extra={...}` become top-level keys.
        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRS and not key.startswith("_"):
                entry[key] = value

        if record.exc_info and record.exc_info[0] is not None:
            # Exception type only; tracebacks can contain document text or secrets.
            entry["exc_type"] = record.exc_info[0].__name__

        return json.dumps(entry, default=str)


def configure_logging() -> None:
    root = logging.getLogger()
    if any(getattr(h, "_documind", False) for h in root.handlers):
        return

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler._documind = True  # type: ignore[attr-defined]

    root.handlers = [handler]
    root.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())

    # Keep third-party HTTP clients from logging request URLs or headers.
    for noisy in ("httpx", "httpcore", "google_genai", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
