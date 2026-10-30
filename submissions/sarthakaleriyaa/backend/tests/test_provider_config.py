"""Gemini model configuration and provider rate-limit handling (no network)."""

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from google.genai import errors

from app.services import document_processor, rag
from app.services.rag import AIProviderRateLimited, AIProviderUnavailable
from tests.helpers import auth, signup_and_login

SUBMISSION = Path(__file__).resolve().parents[2]


def rate_limit_error(marker: str = "quota detail must not leak") -> errors.ClientError:
    return errors.ClientError(
        429, {"error": {"status": "RESOURCE_EXHAUSTED", "message": marker}}
    )


@pytest.mark.skipif(
    bool(os.getenv("GEMINI_GENERATION_MODEL")), reason="model overridden in this shell"
)
def test_default_generation_model_is_the_free_tier_flash_lite():
    assert rag.GENERATION_MODEL == "gemini-3.5-flash-lite"
    assert rag.EMBEDDING_MODEL == "gemini-embedding-001"


def test_model_defaults_match_env_example_and_compose():
    env_example = (SUBMISSION / ".env.example").read_text(encoding="utf-8")
    compose = (SUBMISSION / "docker-compose.yml").read_text(encoding="utf-8")

    for name, default in (
        ("GEMINI_GENERATION_MODEL", "gemini-3.5-flash-lite"),
        ("GEMINI_EMBEDDING_MODEL", "gemini-embedding-001"),
    ):
        assert re.search(rf"^{name}={re.escape(default)}$", env_example, re.M)
        assert f"{name}: ${{{name}:-{default}}}" in compose


def test_generation_model_can_be_overridden_by_environment():
    env = {
        **os.environ,
        "DOCUMIND_DISABLE_DOTENV": "1",
        "GEMINI_GENERATION_MODEL": "gemini-3.8-flash",
    }
    result = subprocess.run(
        [sys.executable, "-c", "from app.services import rag; print(rag.GENERATION_MODEL)"],
        cwd=SUBMISSION / "backend",
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )

    assert result.stdout.strip() == "gemini-3.8-flash"


def test_sdk_retries_are_bounded_and_skip_rate_limits(monkeypatch):
    captured = {}

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setenv("GEMINI_API_KEY", "test-api-key")
    monkeypatch.setenv("GEMINI_RETRY_ATTEMPTS", "2")
    monkeypatch.setattr(rag.genai, "Client", FakeClient)

    rag.get_gemini_client()
    retry = captured["http_options"].retry_options

    assert retry.attempts == 2
    assert 429 not in retry.http_status_codes
    # 504 is our own X-Server-Timeout expiring; retrying with the same deadline
    # timed out again in the live checks.
    assert 504 not in retry.http_status_codes
    assert {500, 502, 503} <= set(retry.http_status_codes)


def test_generation_429_becomes_rate_limited_without_leaking_body(caplog):
    marker = "quota detail must not leak"

    class FakeModels:
        def generate_content(self, **kwargs):
            raise rate_limit_error(marker)

    class FakeClient:
        models = FakeModels()

    caplog.set_level("WARNING", logger=rag.logger.name)

    with pytest.raises(AIProviderRateLimited) as exc_info:
        rag.generate_with_usage(FakeClient(), "prompt", user_id=1)

    # Still an AIProviderUnavailable, so existing handlers keep working.
    assert isinstance(exc_info.value, AIProviderUnavailable)
    assert marker not in str(exc_info.value)
    assert marker not in caplog.text
    assert "status=429" in caplog.text


def test_embedding_429_becomes_rate_limited():
    class FakeModels:
        def embed_content(self, **kwargs):
            raise rate_limit_error()

    class FakeClient:
        models = FakeModels()

    with pytest.raises(AIProviderRateLimited):
        rag.embed_texts(FakeClient(), ["text"], "RETRIEVAL_QUERY")


def test_other_provider_errors_stay_generic_unavailable():
    class FakeModels:
        def embed_content(self, **kwargs):
            raise errors.ServerError(503, {"error": {"status": "UNAVAILABLE"}})

    class FakeClient:
        models = FakeModels()

    with pytest.raises(AIProviderUnavailable) as exc_info:
        rag.embed_texts(FakeClient(), ["text"], "RETRIEVAL_QUERY")

    assert not isinstance(exc_info.value, AIProviderRateLimited)


def test_chat_reports_usage_limit_with_retry_after(client, monkeypatch):
    def rate_limited(**kwargs):
        raise AIProviderRateLimited("AI provider rate limit or quota reached")

    monkeypatch.setattr("app.api.chat.answer_question", rate_limited)
    token = signup_and_login(client, "quota@example.com")

    response = client.post("/chat", json={"question": "Anything?"}, headers=auth(token))

    assert response.status_code == 503
    assert response.headers["Retry-After"] == "60"
    assert response.json()["detail"] == (
        "The AI service has reached its usage limit. Please try again later."
    )
    history = client.get("/chat/history", headers=auth(token)).json()
    assert history[0]["status"] == "error"


def test_ingestion_rate_limit_retries_with_backoff_then_names_the_cause(
    db_session, session_factory, monkeypatch, tmp_path
):
    from app import worker
    from app.models.document import Document
    from app.models.user import User

    def limited_client():
        raise AIProviderRateLimited("quota")

    monkeypatch.setattr(document_processor, "SessionLocal", session_factory)
    monkeypatch.setattr(worker, "SessionLocal", session_factory)
    monkeypatch.setattr(document_processor, "get_ai_client", limited_client)
    monkeypatch.setenv("WORKER_RETRY_BASE_SECONDS", "0")

    user = User(email="limits@example.com", hashed_password="x")
    db_session.add(user)
    db_session.commit()
    path = tmp_path / "a.txt"
    path.write_text("Some text to embed.", encoding="utf-8")
    document = Document(
        user_id=user.id, filename="a.txt", stored_path=str(path),
        content_type="text/plain", size_bytes=19, status="queued",
    )
    db_session.add(document)
    db_session.commit()

    for _ in range(document_processor.MAX_ATTEMPTS):
        worker.run_once()

    db_session.expire_all()
    final = db_session.get(Document, document.id)
    assert final.status == "failed"
    assert final.attempts == document_processor.MAX_ATTEMPTS
    assert final.failure_reason == (
        "The AI provider's usage limit was reached; try uploading again later"
    )


def test_generation_504_is_a_provider_error_not_a_database_error(client, monkeypatch):
    """Live-check regression: a Gemini 504 after retrieval was reported as
    "database temporarily unavailable". Runs the real answer_question and
    chat route; only retrieval and the provider are faked."""
    from collections import namedtuple
    from types import SimpleNamespace

    Row = namedtuple("Row", "chunk document distance")
    sessions = []

    def fake_retrieve(db, user_id, query_vector, document_ids, top_k):
        sessions.append(db)
        chunk = SimpleNamespace(chunk_index=0, page_number=None, content="Hotel bookings are reimbursed up to 160 EUR.")
        document = SimpleNamespace(id=1, filename="kestrel_expense_policy.txt")
        return [Row(chunk, document, 0.35)]

    class FakeModels:
        def embed_content(self, **kwargs):
            return SimpleNamespace(embeddings=[SimpleNamespace(values=[0.1] * 768)])

        def generate_content(self, **kwargs):
            # The fix: no database transaction may be open during the model call.
            assert not sessions[-1].in_transaction(), "transaction held during LLM call"
            raise errors.ServerError(504, {"error": {"status": "DEADLINE_EXCEEDED"}})

    monkeypatch.setattr(rag, "retrieve_chunks", fake_retrieve)
    monkeypatch.setattr(rag, "get_ai_client", lambda: SimpleNamespace(models=FakeModels()))
    token = signup_and_login(client, "deadline@example.com")

    response = client.post(
        "/chat", json={"question": "What is the hotel limit?"}, headers=auth(token)
    )

    assert response.status_code == 503
    assert response.json()["detail"].startswith("The AI service is temporarily unavailable")
    assert "database" not in response.text.lower()
    assert client.get("/chat/history", headers=auth(token)).json()[0]["status"] == "error"


def test_provider_error_is_reported_even_if_saving_history_fails(client, monkeypatch):
    from sqlalchemy.exc import OperationalError

    def provider_down(**kwargs):
        raise AIProviderUnavailable("down")

    calls = {"n": 0}
    real_finish = __import__("app.api.chat", fromlist=["_finish"])._finish

    def finish_fails_on_error_path(db, record, **fields):
        calls["n"] += 1
        if fields.get("status") == "error":
            raise OperationalError("UPDATE question_history", {}, Exception("connection lost"))
        return real_finish(db, record, **fields)

    monkeypatch.setattr("app.api.chat.answer_question", provider_down)
    monkeypatch.setattr("app.api.chat._finish", finish_fails_on_error_path)
    token = signup_and_login(client, "bookkeeping@example.com")

    response = client.post("/chat", json={"question": "Anything?"}, headers=auth(token))

    assert calls["n"] == 1
    assert response.status_code == 503
    assert response.json()["detail"].startswith("The AI service is temporarily unavailable")


@pytest.mark.parametrize(
    "variable,value",
    [("AI_PROVIDER", "gemni"), ("AI_PROVIDER", "openai"), ("GEMINI_THINKING_LEVEL", "LOWEST")],
)
def test_startup_rejects_unknown_ai_settings(monkeypatch, variable, value):
    from app.core.config import validate_ai_settings

    monkeypatch.setenv(variable, value)

    with pytest.raises(RuntimeError, match=variable):
        validate_ai_settings()


def test_valid_ai_settings_pass(monkeypatch):
    from app.core.config import validate_ai_settings

    for provider in ("gemini", "LOCAL"):
        for level in ("minimal", "LOW", "medium", "HIGH"):
            monkeypatch.setenv("AI_PROVIDER", provider)
            monkeypatch.setenv("GEMINI_THINKING_LEVEL", level)
            validate_ai_settings()


@pytest.mark.parametrize("level,expected", [(None, "LOW"), ("minimal", "MINIMAL"), ("HIGH", "HIGH")])
def test_thinking_level_reaches_the_generation_request(monkeypatch, level, expected):
    from types import SimpleNamespace

    from google.genai import types

    if level is None:
        monkeypatch.delenv("GEMINI_THINKING_LEVEL", raising=False)
    else:
        monkeypatch.setenv("GEMINI_THINKING_LEVEL", level)
    captured = {}

    class FakeModels:
        def generate_content(self, *, model, contents, config):
            captured["config"] = config
            return SimpleNamespace(text="ok [S1]", usage_metadata=None)

    rag.generate_with_usage(SimpleNamespace(models=FakeModels()), "prompt", user_id=1)

    assert captured["config"].thinking_config.thinking_level == types.ThinkingLevel[expected]
