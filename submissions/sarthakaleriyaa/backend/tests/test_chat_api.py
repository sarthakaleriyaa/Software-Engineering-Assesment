from app.services.rag import AIProviderUnavailable
from tests.helpers import auth, signup_and_login


def fake_answer(**kwargs):
    return {
        "answer": "Refunds take 5 days [S1].",
        "refused": False,
        "citations": [
            {
                "source_id": "S1",
                "document_id": 1,
                "filename": "policy.txt",
                "chunk_index": 0,
                "page_number": 2,
                "passage": "Refunds take 5 days.",
                "score": 0.82,
            }
        ],
        "input_tokens": 100,
        "output_tokens": 20,
        "latency_ms": 42,
        "estimated_cost_usd": None,
    }


def ask(client, token, question="How long do refunds take?", **extra):
    return client.post(
        "/chat", json={"question": question, **extra}, headers=auth(token)
    )


def test_chat_returns_answer_citations_and_usage(client, monkeypatch):
    monkeypatch.setattr("app.api.chat.answer_question", fake_answer)
    token = signup_and_login(client, "u@example.com")

    response = ask(client, token)

    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == "Refunds take 5 days [S1]."
    assert body["refused"] is False
    assert body["citations"][0]["page_number"] == 2
    assert body["usage"] == {
        "input_tokens": 100,
        "output_tokens": 20,
        "latency_ms": 42,
        "estimated_cost_usd": None,
    }
    assert isinstance(body["question_id"], int)


def test_history_is_stored_and_private_per_user(client, monkeypatch):
    monkeypatch.setattr("app.api.chat.answer_question", fake_answer)
    alice = signup_and_login(client, "alice@example.com")
    bob = signup_and_login(client, "bob@example.com")

    ask(client, alice, "First question?")
    ask(client, alice, "Second question?")

    alice_history = client.get("/chat/history", headers=auth(alice)).json()
    bob_history = client.get("/chat/history", headers=auth(bob)).json()

    assert [h["question"] for h in alice_history] == [
        "Second question?",
        "First question?",
    ]
    assert alice_history[0]["status"] == "answered"
    assert alice_history[0]["citations"][0]["filename"] == "policy.txt"
    assert alice_history[0]["usage"]["latency_ms"] == 42
    assert bob_history == []


def test_history_requires_authentication(client):
    assert client.get("/chat/history").status_code == 401


def test_rate_limit_is_per_user(client, monkeypatch):
    monkeypatch.setattr("app.api.chat.answer_question", fake_answer)
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "2")
    alice = signup_and_login(client, "ra@example.com")
    bob = signup_and_login(client, "rb@example.com")

    assert ask(client, alice).status_code == 200
    assert ask(client, alice).status_code == 200
    limited = ask(client, alice)

    assert limited.status_code == 429
    assert limited.headers["Retry-After"] == "60"
    # Another user's budget is unaffected.
    assert ask(client, bob).status_code == 200


def test_failed_attempts_count_toward_rate_limit(client, monkeypatch):
    def fail(**kwargs):
        raise AIProviderUnavailable("down")

    monkeypatch.setattr("app.api.chat.answer_question", fail)
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "1")
    token = signup_and_login(client, "rf@example.com")

    assert ask(client, token).status_code == 503
    assert ask(client, token).status_code == 429

    history = client.get("/chat/history", headers=auth(token)).json()
    assert history[0]["status"] == "error"


def test_unexpected_error_returns_generic_message(client, monkeypatch):
    secret_detail = "postgresql://admin:hunter2@db/internal"

    def explode(**kwargs):
        raise RuntimeError(secret_detail)

    monkeypatch.setattr("app.api.chat.answer_question", explode)
    token = signup_and_login(client, "boom@example.com")

    response = ask(client, token)

    assert response.status_code == 500
    assert "hunter2" not in response.text
    assert response.json()["detail"] == (
        "An unexpected error occurred while processing your question."
    )


def test_validation_errors_do_not_echo_passwords(client):
    response = client.post(
        "/auth/signup", json={"email": "not-an-email", "password": "SuperSecret99"}
    )

    assert response.status_code == 422
    assert "SuperSecret99" not in response.text


def test_question_length_is_bounded(client):
    token = signup_and_login(client, "long@example.com")

    response = ask(client, token, "x" * 4001)

    assert response.status_code == 422
