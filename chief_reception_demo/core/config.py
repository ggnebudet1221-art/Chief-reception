from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    owner_telegram_id: int
    database_path: Path
    salon_name: str
    timezone: str
    anthropic_api_key: str
    anthropic_base_url: str
    anthropic_model: str
    claude_max_tokens: int


def load_settings(env_path: str = ".env.demo") -> Settings:
    if Path(env_path).exists():
        load_dotenv(env_path)
    else:
        load_dotenv(".env_demo")

    token = os.getenv("RECEPTION_TELEGRAM_BOT_TOKEN", "").strip()
    owner_id = os.getenv("RECEPTION_OWNER_TELEGRAM_ID", "0").strip()
    database_path = Path(os.getenv("RECEPTION_DATABASE_PATH", "demo_data/reception_demo.sqlite3"))

    if not token:
        raise RuntimeError("Set RECEPTION_TELEGRAM_BOT_TOKEN in .env.demo")

    try:
        parsed_owner_id = int(owner_id)
    except ValueError as exc:
        raise RuntimeError("RECEPTION_OWNER_TELEGRAM_ID must be a Telegram numeric id") from exc

    if parsed_owner_id <= 0:
        raise RuntimeError("Set RECEPTION_OWNER_TELEGRAM_ID in .env.demo")

    timezone = os.getenv("RECEPTION_TIMEZONE", "UTC").strip() or "UTC"
    timezone = safe_timezone_key(timezone)
    max_tokens = parse_positive_int(os.getenv("CLAUDE_MAX_TOKENS", "300"), default=300)

    return Settings(
        telegram_bot_token=token,
        owner_telegram_id=parsed_owner_id,
        database_path=database_path,
        salon_name=os.getenv("RECEPTION_SALON_NAME", "Beauty Room").strip() or "Beauty Room",
        timezone=timezone,
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", "").strip(),
        anthropic_base_url=os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com").strip()
        or "https://api.anthropic.com",
        anthropic_model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5").strip() or "claude-sonnet-4-5",
        claude_max_tokens=max_tokens,
    )


def safe_timezone_key(timezone: str) -> str:
    if timezone in {"UTC", "Europe/Moscow"}:
        return timezone

    try:
        ZoneInfo(timezone)
    except ZoneInfoNotFoundError:
        logger.warning("Invalid timezone %r. Falling back to UTC.", timezone)
        return "UTC"
    return timezone


def parse_positive_int(value: str, *, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        logger.warning("Invalid CLAUDE_MAX_TOKENS=%r. Falling back to %s.", value, default)
        return default
    if parsed <= 0:
        logger.warning("CLAUDE_MAX_TOKENS must be positive. Falling back to %s.", default)
        return default
    return parsed
