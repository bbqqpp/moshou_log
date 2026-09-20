from __future__ import annotations

import secrets

from .config import settings

# 登录凭据从环境变量 `ALLOWED_GUILD_NAME` 读，不再硬编码 —— 仓库是公开的，
# 源码里写死等于把口令公开。
ALLOWED_GUILD_NAME = settings.allowed_guild_name

_tokens: set[str] = set()


def validate_guild_name(guild_name: str) -> bool:
    # 没配就一律拒绝（fail closed）。否则空字符串会成为万能口令：
    # 任何人提交一个空公会名都能通过校验。
    if not ALLOWED_GUILD_NAME:
        return False
    return guild_name.strip() == ALLOWED_GUILD_NAME


def issue_token() -> str:
    token = secrets.token_urlsafe(32)
    _tokens.add(token)
    return token


def is_token_valid(token: str) -> bool:
    return bool(token) and token in _tokens
