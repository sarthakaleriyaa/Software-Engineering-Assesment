import httpx
import httpx2
import pytest
from google.genai import errors, types

from app.services import rag
from app.services.rag import AIProviderUnavailable, DocumentSelectionError


def create_authenticated_user(client) -> str:
    signup_response = client.post(
        "/auth/signup",
        json={
            "email": "chat@example.com",
            "password": "CorrectPassword123",
        },
    )
    assert signup_response.status_code == 201

    login_response = client.post(
        "/auth/login",
        json={
            "email": "chat@example.com",
            "password": "CorrectPassword123",
        },
    )
    assert login_response.status_code == 200

    return login_response.json()["access_token"]


def test_chat_redacts_credentials_in_answer_and_citations(
    client,
    monkeypatch,
):
    token = create_authenticated_user(client)
    key = "AIza" + ("B" * 35)

    def fake_answer_question(**kwargs):
        return {
            "answer": f"The API key is {key}",
            "citations": [
                {
                    "document_id": 1,
                    "filename": "report.txt",
                    "chunk_index": 0,
                    "page_number": None,
                    "passage": "password=hunter2 and token: abcdefgh",
                }
            ],
        }

    monkeypatch.setattr(
        "app.api.chat.answer_question",
        fake_answer_question,
    )

    response = client.post(
        "/chat",
        json={"question": "What is the key?"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200

    body = response.json()
    assert key not in body["answer"]
    assert "[REDACTED]" in body["answer"]
    assert "hunter2" not in body["citations"][0]["passage"]
    assert "abcdefgh" not in body["citations"][0]["passage"]


def test_chat_provider_unavailable_returns_503(client, monkeypatch):
    token = create_authenticated_user(client)

    def fail_answer_question(**kwargs):
        raise AIProviderUnavailable("provider unavailable")

    monkeypatch.setattr(
        "app.api.chat.answer_question",
        fail_answer_question,
    )

    response = client.post(
        "/chat",
        json={"question": "What is the key?"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 503
    assert response.json() == {
        "detail": (
            "The AI service is temporarily unavailable. "
            "Please try again shortly."
        )
    }


def test_chat_invalid_document_returns_404(client, monkeypatch):
    token = create_authenticated_user(client)

    def fail_answer_question(**kwargs):
        raise DocumentSelectionError("One or more selected documents are unavailable")

    monkeypatch.setattr(
        "app.api.chat.answer_question",
        fail_answer_question,
    )

    response = client.post(
        "/chat",
        json={"question": "What is in it?", "document_ids": [999]},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 404


def test_rag_maps_provider_timeout_to_unavailable(monkeypatch):
    class FakeModels:
        def embed_content(self, *args, **kwargs):
            raise httpx.ReadTimeout("timed out")

    class FakeClient:
        models = FakeModels()

    monkeypatch.setattr(
        rag,
        "get_gemini_client",
        lambda: FakeClient(),
    )

    with pytest.raises(AIProviderUnavailable):
        rag.answer_question(
            db=None,
            user_id=1,
            question="What is in the document?",
        )


def test_rag_maps_httpx2_timeout_to_unavailable(monkeypatch):
    class FakeModels:
        def embed_content(self, *args, **kwargs):
            raise httpx2.ReadTimeout("timed out")

    class FakeClient:
        models = FakeModels()

    monkeypatch.setattr(
        rag,
        "get_gemini_client",
        lambda: FakeClient(),
    )

    with pytest.raises(AIProviderUnavailable):
        rag.answer_question(
            db=None,
            user_id=1,
            question="What is in the document?",
        )


def test_generation_504_returns_generic_unavailable(caplog):
    marker = "provider response body must not leak"

    class FakeModels:
        def generate_content(self, *args, **kwargs):
            raise errors.ServerError(
                504,
                {
                    "error": {
                        "status": "DEADLINE_EXCEEDED",
                        "message": marker,
                    }
                },
            )

    class FakeClient:
        models = FakeModels()

    caplog.set_level("WARNING", logger=rag.logger.name)

    with pytest.raises(AIProviderUnavailable) as exc_info:
        rag._generate_answer(
            FakeClient(),
            prompt="What is in the document?",
            user_id=7,
        )

    assert str(exc_info.value) == "AI provider is temporarily unavailable"
    assert marker not in str(exc_info.value)
    assert marker not in caplog.text
    assert "status=504" in caplog.text


def test_generation_uses_low_thinking_output_cap_and_redaction():
    key = "AIza" + ("D" * 35)
    captured = {}

    class FakeResponse:
        text = f"The API key is {key}"

    class FakeModels:
        def generate_content(self, *, model, contents, config):
            captured["model"] = model
            captured["contents"] = contents
            captured["config"] = config
            return FakeResponse()

    class FakeClient:
        models = FakeModels()

    answer = rag._generate_answer(
        FakeClient(),
        prompt="What is the API key?",
        user_id=7,
    )

    assert key not in answer
    assert "[REDACTED]" in answer
    assert captured["model"] == rag.GENERATION_MODEL
    assert captured["config"].max_output_tokens == 1024
    assert (
        captured["config"].thinking_config.thinking_level
        == types.ThinkingLevel.LOW
    )


def test_gemini_client_keeps_bounded_timeout_and_attempts(monkeypatch):
    captured = {}

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setenv("GEMINI_API_KEY", "test-api-key")
    monkeypatch.delenv("GEMINI_TIMEOUT_MS", raising=False)
    monkeypatch.delenv("GEMINI_RETRY_ATTEMPTS", raising=False)
    monkeypatch.setattr(rag.genai, "Client", FakeClient)

    client = rag.get_gemini_client()

    assert isinstance(client, FakeClient)
    assert captured["http_options"].timeout == 15000
    assert captured["http_options"].retry_options.attempts == 2
    assert 429 not in captured["http_options"].retry_options.http_status_codes


def test_rag_missing_api_key_is_provider_unavailable(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    with pytest.raises(AIProviderUnavailable):
        rag.answer_question(
            db=None,
            user_id=1,
            question="What is in the document?",
        )


def test_unrelated_value_error_is_a_generic_500_not_404(client, monkeypatch):
    token = create_authenticated_user(client)
    internal = "invalid literal for int() with base 10: 'internal detail'"

    def fail_answer_question(**kwargs):
        raise ValueError(internal)

    monkeypatch.setattr("app.api.chat.answer_question", fail_answer_question)

    response = client.post(
        "/chat",
        json={"question": "What is in it?"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 500
    assert "internal detail" not in response.text
