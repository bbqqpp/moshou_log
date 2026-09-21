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
    # 登录用的公会名。**不要硬编码回源码里** —— 这个仓库是公开的，
    # 写死等于把进入口令贴在 README 上。没配就一律拒绝登录，见 auth.py。
    allowed_guild_name: str = os.getenv("ALLOWED_GUILD_NAME", "")

    wcl_client_id: str = os.getenv("WCL_CLIENT_ID", "")
    wcl_client_secret: str = os.getenv("WCL_CLIENT_SECRET", "")

    deepseek_api_key: str = os.getenv("DEEPSEEK_API_KEY", "")
    deepseek_base_url: str = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    deepseek_model: str = os.getenv("DEEPSEEK_MODEL", "deepseek-flash")
    deepseek_max_tokens: int = _int("DEEPSEEK_MAX_TOKENS", 8000)

    max_wcl_event_pages: int = _int("MAX_WCL_EVENT_PAGES", 200)
    timeline_preview_limit: int = _int("TIMELINE_PREVIEW_LIMIT", 1000)
    request_timeout: float = _float("REQUEST_TIMEOUT", 60)


settings = Settings()
