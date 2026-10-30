import os

from sqlalchemy import create_engine
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.core.config import env_int as _env_int

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not configured")


def build_connect_args(url: URL) -> dict[str, object]:
    if url.get_backend_name() != "postgresql":
        return {}

    connect_args: dict[str, object] = {
        "connect_timeout": _env_int("DB_CONNECT_TIMEOUT_SECONDS", 5),
        "options": (
            "-c statement_timeout="
            f"{_env_int('DB_STATEMENT_TIMEOUT_MS', 15000)} "
            "-c idle_in_transaction_session_timeout="
            f"{_env_int('DB_IDLE_TRANSACTION_TIMEOUT_MS', 15000)}"
        ),
    }

    if (url.host or "").lower() == "localhost":
        connect_args["hostaddr"] = "127.0.0.1"

    return connect_args


def normalize_database_url(raw: str) -> str:
    """Use the psycopg 3 driver for plain Postgres URLs.

    Hosted Postgres (Render, Railway, Heroku) hands out postgres:// or
    postgresql:// URLs, which SQLAlchemy maps to psycopg2; only psycopg 3 is
    installed here.
    """
    for prefix in ("postgres://", "postgresql://"):
        if raw.startswith(prefix):
            return "postgresql+psycopg://" + raw[len(prefix):]
    return raw


database_url = make_url(normalize_database_url(DATABASE_URL))

engine = create_engine(
    database_url,
    pool_pre_ping=True,
    pool_timeout=_env_int("DB_POOL_TIMEOUT_SECONDS", 5),
    pool_recycle=_env_int("DB_POOL_RECYCLE_SECONDS", 1800),
    connect_args=build_connect_args(database_url),
)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        raise
    finally:
        db.close()