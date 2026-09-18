from __future__ import annotations

import secrets

ALLOWED_GUILD_NAME = "圣光的祝福"

_tokens: set[str] = set()


def validate_guild_name(guild_name: str) -> bool:
    return guild_name.strip() == ALLOWED_GUILD_NAME


def issue_token() -> str:
    token = secrets.token_urlsafe(32)
    _tokens.add(token)
    return token


def is_token_valid(token: str) -> bool:
    return bool(token) and token in _tokens
