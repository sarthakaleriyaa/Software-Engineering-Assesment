from app.services.redaction import REDACTED, redact_sensitive


def test_gemini_api_key_is_redacted():
    key = "AIza" + ("A" * 35)

    result = redact_sensitive(f"The configured key is {key}.")

    assert key not in result
    assert REDACTED in result


def test_password_assignment_is_redacted():
    result = redact_sensitive("The config says password = hunter2.")

    assert "hunter2" not in result
    assert "password = [REDACTED]" in result


def test_bearer_token_is_redacted():
    result = redact_sensitive(
        "Authorization: Bearer abcdefghijklmnop"
    )

    assert "abcdefghijklmnop" not in result
    assert "Bearer [REDACTED]" in result


def test_database_url_password_is_redacted():
    result = redact_sensitive(
        "postgresql://documind:supersecret@db:5432/documind"
    )

    assert "supersecret" not in result
    assert "documind:[REDACTED]@db" in result


def test_private_key_block_is_redacted():
    text = (
        "-----BEGIN PRIVATE KEY-----\n"
        "secret-material\n"
        "-----END PRIVATE KEY-----"
    )

    result = redact_sensitive(text)

    assert "secret-material" not in result
    assert "[REDACTED PRIVATE KEY]" in result


def test_normal_password_policy_text_is_not_redacted():
    text = "The password policy requires at least 12 characters."

    assert redact_sensitive(text) == text
