import os
from pathlib import Path

from dotenv import load_dotenv

_BACKEND_DIR = Path(__file__).resolve().parents[2]
_loaded = False


def load_environment() -> None:
    """Load .env files once, unless disabled (tests set DOCUMIND_DISABLE_DOTENV)."""
    global _loaded

    if _loaded or os.getenv("DOCUMIND_DISABLE_DOTENV"):
        return

    # Real environment variables always win over .env values.
    load_dotenv(_BACKEND_DIR / ".env")
    load_dotenv(_BACKEND_DIR.parent / ".env")
    _loaded = True


def env_int(name: str, default: int, minimum: int = 1) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default

    return max(minimum, value)


def env_float(name: str, default: float | None) -> float | None:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default

    try:
        return float(raw)
    except ValueError:
        return default


def upload_dir() -> Path:
    configured = os.getenv("UPLOAD_DIR")
    if configured:
        return Path(configured)

    return _BACKEND_DIR / "storage" / "uploads"


AI_PROVIDERS = ("gemini", "local")
THINKING_LEVELS = ("MINIMAL", "LOW", "MEDIUM", "HIGH")


def ai_provider() -> str:
    """'gemini' (default) or 'local' (offline, deterministic, no network)."""
    return os.getenv("AI_PROVIDER", "gemini").strip().lower()


def thinking_level() -> str:
    return os.getenv("GEMINI_THINKING_LEVEL", "LOW").strip().upper()


def validate_ai_settings() -> None:
    """Fail at startup on a misspelt provider or thinking level, instead of
    silently treating it as Gemini or failing on the first question."""
    if ai_provider() not in AI_PROVIDERS:
        raise RuntimeError(f"AI_PROVIDER must be one of {', '.join(AI_PROVIDERS)}")
    if thinking_level() not in THINKING_LEVELS:
        raise RuntimeError(
            f"GEMINI_THINKING_LEVEL must be one of {', '.join(THINKING_LEVELS)}"
        )


load_environment()
