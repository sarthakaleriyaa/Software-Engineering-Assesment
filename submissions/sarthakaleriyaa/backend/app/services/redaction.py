import re

REDACTED = "[REDACTED]"

_PRIVATE_KEY_PATTERN = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
    re.DOTALL | re.IGNORECASE,
)
_DATABASE_URL_PATTERN = re.compile(
    r"(?i)\b(postgres(?:ql)?(?:\+[a-z0-9_]+)?://[^:\s/@]+:)([^@\s]+)(@)"
)
_JWT_PATTERN = re.compile(
    r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"
)
_GEMINI_KEY_PATTERN = re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b")
_AWS_KEY_PATTERN = re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")
_GITHUB_TOKEN_PATTERN = re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")
_OPENAI_KEY_PATTERN = re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")
_BEARER_PATTERN = re.compile(
    r"(?i)\b(bearer\s+)([A-Za-z0-9._~+/=-]{8,})"
)
_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)\b("
    r"api[_-]?key|secret|password|passwd|pwd|token|access[_-]?token|"
    r"refresh[_-]?token|private[_-]?key"
    r")\b(\s*[:=]\s*)([\"']?)([^\s\"',;]{4,})\3"
)
_PROSE_CREDENTIAL_PATTERN = re.compile(
    r"(?i)\b(password|passphrase|secret)\b"
    r"(\s+(?:is|was)\s+)([\"']?)([^\s.,;]{4,})\3"
)

_KEY_PATTERNS = (
    _JWT_PATTERN,
    _GEMINI_KEY_PATTERN,
    _AWS_KEY_PATTERN,
    _GITHUB_TOKEN_PATTERN,
    _OPENAI_KEY_PATTERN,
)


def redact_sensitive(text: str) -> str:
    if not text:
        return text

    redacted = _PRIVATE_KEY_PATTERN.sub("[REDACTED PRIVATE KEY]", text)
    redacted = _DATABASE_URL_PATTERN.sub(
        rf"\1{REDACTED}\3",
        redacted,
    )
    redacted = _BEARER_PATTERN.sub(rf"\1{REDACTED}", redacted)
    redacted = _ASSIGNMENT_PATTERN.sub(
        rf"\1\2\3{REDACTED}\3",
        redacted,
    )
    redacted = _PROSE_CREDENTIAL_PATTERN.sub(
        rf"\1\2\3{REDACTED}\3",
        redacted,
    )

    for pattern in _KEY_PATTERNS:
        redacted = pattern.sub(REDACTED, redacted)

    return redacted
