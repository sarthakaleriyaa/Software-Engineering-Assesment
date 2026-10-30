from datetime import datetime, timedelta, timezone

import jwt
from sqlalchemy.exc import OperationalError

from app.core.database import get_db
from app.core.security import JWT_ALGORITHM, JWT_SECRET_KEY
from app.main import app

EMAIL = "user@example.com"
PASSWORD = "CorrectPassword123"


def signup(client, email: str = EMAIL, password: str = PASSWORD):
    return client.post(
        "/auth/signup",
        json={"email": email, "password": password},
    )


def login(client, email: str = EMAIL, password: str = PASSWORD):
    return client.post(
        "/auth/login",
        json={"email": email, "password": password},
    )


def test_signup_and_login_return_jwt(client):
    signup_response = signup(client)
    assert signup_response.status_code == 201

    login_response = login(client)
    assert login_response.status_code == 200

    body = login_response.json()
    assert body["token_type"] == "bearer"
    assert body["user"]["email"] == EMAIL

    payload = jwt.decode(
        body["access_token"],
        JWT_SECRET_KEY,
        algorithms=[JWT_ALGORITHM],
        options={"require": ["sub", "exp"]},
    )
    assert payload["sub"] == str(body["user"]["id"])


def test_invalid_password_returns_401(client):
    assert signup(client).status_code == 201

    response = login(client, password="WrongPassword123")

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid email or password"


def test_unknown_user_returns_401(client):
    response = login(client, email="missing@example.com")

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid email or password"


def test_duplicate_signup_returns_409(client):
    assert signup(client).status_code == 201

    response = signup(client)

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "An account with this email already exists"
    )


def test_protected_endpoint_rejects_missing_invalid_and_expired_tokens(
    client,
):
    assert signup(client).status_code == 201
    token = login(client).json()["access_token"]

    missing = client.get("/auth/me")
    assert missing.status_code == 401

    invalid = client.get(
        "/auth/me",
        headers={"Authorization": "Bearer not-a-valid-token"},
    )
    assert invalid.status_code == 401

    expired_token = jwt.encode(
        {
            "sub": "1",
            "exp": datetime.now(timezone.utc) - timedelta(seconds=5),
        },
        JWT_SECRET_KEY,
        algorithm=JWT_ALGORITHM,
    )
    expired = client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {expired_token}"},
    )
    assert expired.status_code == 401

    valid = client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert valid.status_code == 200
    assert valid.json()["email"] == EMAIL


def test_login_does_not_require_gemini(client, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    assert signup(client).status_code == 201
    response = login(client)

    assert response.status_code == 200


def test_database_failure_returns_503(client):
    class BrokenSession:
        def scalar(self, *args, **kwargs):
            raise OperationalError(
                "SELECT 1",
                {},
                Exception("database down"),
            )

        def close(self):
            pass

    def broken_get_db():
        yield BrokenSession()

    app.dependency_overrides[get_db] = broken_get_db

    response = client.post(
        "/auth/login",
        json={"email": EMAIL, "password": PASSWORD},
    )

    assert response.status_code == 503
    assert "temporarily unavailable" in response.json()["detail"]


def test_unknown_email_still_runs_password_verification(client, monkeypatch):
    """Login timing must not reveal whether an email has an account."""
    calls = []
    monkeypatch.setattr(
        "app.api.auth.verify_password_against_dummy",
        lambda password: calls.append(password),
    )

    response = login(client, email="nobody@example.com")

    assert response.status_code == 401
    assert calls == [PASSWORD]


def test_short_jwt_secrets_are_flagged():
    from app.core.security import jwt_secret_is_weak

    assert jwt_secret_is_weak("short")
    assert jwt_secret_is_weak(None)
    assert not jwt_secret_is_weak("x" * 32)
