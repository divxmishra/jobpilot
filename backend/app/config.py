"""Runtime configuration.

Secrets are read from the process environment or a local `.env` file (never
committed). Nothing in this module is ever sent to the browser except the
non-secret flags exposed by `public_config()`.
"""
from __future__ import annotations

import os
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader (no extra dependency). Existing env vars win."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv(ROOT / ".env")


def _bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


class Settings:
    def __init__(self) -> None:
        self.env = os.getenv("JOBPILOT_ENV", "development").lower()
        self.data_dir = Path(os.getenv("JOBPILOT_DATA_DIR", str(ROOT / "data"))).resolve()
        self.frontend_dir = ROOT / "frontend"
        self.password = os.getenv("JOBPILOT_PASSWORD", "")
        self.secret_key = os.getenv("JOBPILOT_SECRET_KEY") or ""
        if not self.secret_key:
            # Ephemeral key: sessions reset on restart. Fine for local use.
            self.secret_key = secrets.token_urlsafe(32)
            self.secret_key_ephemeral = True
        else:
            self.secret_key_ephemeral = False
        self.persistent_storage = _bool("JOBPILOT_PERSISTENT_STORAGE", not self.env == "production")
        # Secure cookies need HTTPS. Only disable for plain-HTTP local container testing.
        self.cookie_secure = _bool("JOBPILOT_COOKIE_SECURE", self.env == "production")
        self.scheduler_enabled = _bool("JOBPILOT_SCHEDULER", True)
        self.discovery_hour = int(os.getenv("JOBPILOT_DISCOVERY_HOUR", "7"))
        self.max_upload_mb = int(os.getenv("JOBPILOT_MAX_UPLOAD_MB", "8"))
        self.http_timeout = float(os.getenv("JOBPILOT_HTTP_TIMEOUT", "20"))
        self.user_agent = os.getenv(
            "JOBPILOT_USER_AGENT",
            "JobPilot/1.0 (personal job-search assistant; respects robots and rate limits)",
        )
        # AI providers (all optional). Keys are never exposed to the frontend.
        self.ai_provider = os.getenv("AI_PROVIDER", "none").lower()  # none | gemini | groq | openai_compatible
        self.gemini_api_key = os.getenv("GEMINI_API_KEY", "")
        self.groq_api_key = os.getenv("GROQ_API_KEY", "")
        self.openai_compat_key = os.getenv("AI_API_KEY", "")
        self.openai_compat_base = os.getenv("AI_BASE_URL", "")
        self.ai_model = os.getenv("AI_MODEL", "")
        # Price per 1M tokens in INR (0 for free tiers). Used for budget tracking.
        self.ai_price_in_per_m = float(os.getenv("AI_PRICE_INPUT_INR_PER_M", "0"))
        self.ai_price_out_per_m = float(os.getenv("AI_PRICE_OUTPUT_INR_PER_M", "0"))

    @property
    def is_production(self) -> bool:
        return self.env == "production"

    @property
    def auth_required(self) -> bool:
        return bool(self.password) or self.is_production

    @property
    def db_path(self) -> Path:
        return self.data_dir / "jobpilot.db"

    def ai_key_present(self) -> bool:
        return {
            "gemini": bool(self.gemini_api_key),
            "groq": bool(self.groq_api_key),
            "openai_compatible": bool(self.openai_compat_key and self.openai_compat_base),
        }.get(self.ai_provider, False)


settings = Settings()


def ensure_dirs() -> None:
    for sub in ["", "uploads", "jobs", "resumes", "reports", "audit", "cv_history", "browser-profile", "tmp"]:
        (settings.data_dir / sub).mkdir(parents=True, exist_ok=True)
