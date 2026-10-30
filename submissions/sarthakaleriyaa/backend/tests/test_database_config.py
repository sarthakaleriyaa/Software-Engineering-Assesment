from sqlalchemy.engine import make_url

from app.core.database import build_connect_args


def test_localhost_is_pinned_to_ipv4(monkeypatch):
    monkeypatch.setenv("DB_CONNECT_TIMEOUT_SECONDS", "5")
    monkeypatch.setenv("DB_STATEMENT_TIMEOUT_MS", "15000")

    args = build_connect_args(
        make_url(
            "postgresql+psycopg://user:password@localhost:5433/documind"
        )
    )

    assert args["hostaddr"] == "127.0.0.1"
    assert args["connect_timeout"] == 5
    assert "statement_timeout=15000" in args["options"]


def test_remote_host_is_not_pinned_to_ipv4():
    args = build_connect_args(
        make_url(
            "postgresql+psycopg://user:password@db.example.com:5432/documind"
        )
    )

    assert "hostaddr" not in args
    assert args["connect_timeout"] >= 1


def test_non_postgres_database_has_no_postgres_args():
    args = build_connect_args(make_url("sqlite:///test.db"))

    assert args == {}


def test_hosted_postgres_urls_use_psycopg3():
    from app.core.database import normalize_database_url

    assert normalize_database_url("postgres://u:p@h:5432/db") == "postgresql+psycopg://u:p@h:5432/db"
    assert normalize_database_url("postgresql://u:p@h/db") == "postgresql+psycopg://u:p@h/db"
    assert normalize_database_url("postgresql+psycopg://u:p@h/db") == "postgresql+psycopg://u:p@h/db"
    assert normalize_database_url("sqlite:///x.db") == "sqlite:///x.db"
