from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ENV_FILE = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(ENV_FILE)


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


@dataclass
class Settings:
    wcl_v1_api_key: str = os.getenv("WCL_V1_API_KEY", "")
    wcl_v1_client_name: str = os.getenv("WCL_V1_CLIENT_NAME", "")

    deepseek_api_key: str = os.getenv("DEEPSEEK_API_KEY", "")
    deepseek_base_url: str = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    deepseek_model: str = os.getenv("DEEPSEEK_MODEL", "deepseek-flash")
    deepseek_max_tokens: int = _int("DEEPSEEK_MAX_TOKENS", 8000)

    max_wcl_event_pages: int = _int("MAX_WCL_EVENT_PAGES", 200)
    max_deepseek_event_chars: int = _int("MAX_DEEPSEEK_EVENT_CHARS", 140000)
    timeline_preview_limit: int = _int("TIMELINE_PREVIEW_LIMIT", 1000)
    request_timeout: float = _float("REQUEST_TIMEOUT", 60)


settings = Settings()
