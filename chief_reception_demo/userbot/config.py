from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class UserbotSettings:
    telegram_api_id: int
    telegram_api_hash: str
    telegram_session_name: str
    anthropic_api_key: str
    anthropic_base_url: str
    anthropic_model: str
    claude_max_tokens: int
    max_history_messages: int
    lead_log_path: Path


def load_userbot_settings(env_path: str = ".env") -> UserbotSettings:
    load_dotenv(env_path)

    api_id = _required_int_any("TELEGRAM_API_ID", "API_ID")
    api_hash = _required_any("TELEGRAM_API_HASH", "API_HASH")
    api_key = _required("ANTHROPIC_API_KEY")

    return UserbotSettings(
        telegram_api_id=api_id,
        telegram_api_hash=api_hash,
        telegram_session_name=os.getenv("TELEGRAM_SESSION_NAME", "demo_data/rahat_float_admin").strip()
        or "demo_data/rahat_float_admin",
        anthropic_api_key=api_key,
        anthropic_base_url=os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com").strip()
        or "https://api.anthropic.com",
        anthropic_model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5").strip() or "claude-sonnet-4-5",
        claude_max_tokens=_optional_int("CLAUDE_MAX_TOKENS", default=600),
        max_history_messages=_optional_int("USERBOT_MAX_HISTORY_MESSAGES", default=8),
        lead_log_path=Path(os.getenv("RAHAT_LEAD_LOG_PATH", "demo_data/rahat_userbot_leads.jsonl")),
    )


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Set {name} in .env")
    return value


def _required_any(*names: str) -> str:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    raise RuntimeError(f"Set one of {', '.join(names)} in .env")


def _required_int(name: str) -> int:
    value = _required(name)
    try:
        return int(value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc


def _required_int_any(*names: str) -> int:
    value = _required_any(*names)
    try:
        return int(value)
    except ValueError as exc:
        raise RuntimeError(f"{'/'.join(names)} must be an integer") from exc


def _optional_int(name: str, *, default: int) -> int:
    value = os.getenv(name, "").strip()
    if not value:
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return parsed if parsed > 0 else default
