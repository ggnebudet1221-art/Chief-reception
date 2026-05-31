from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", case_sensitive=False, extra="ignore")

    app_name: str = Field(default="AI Manager", alias="APP_NAME")
    app_env: Literal["development", "staging", "production"] = Field(default="development", alias="APP_ENV")
    app_host: str = Field(default="0.0.0.0", alias="APP_HOST")
    app_port: int = Field(default=8000, alias="APP_PORT")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")
    anthropic_base_url: str = Field(default="", alias="ANTHROPIC_BASE_URL")
    anthropic_model: str = Field(default="claude-3-7-sonnet-20250219", alias="ANTHROPIC_MODEL")

    database_url: str = Field(default="sqlite+aiosqlite:///./ai_manager.db", alias="DATABASE_URL")
    timezone: str = Field(default="Europe/Amsterdam", alias="TIMEZONE")

    default_system_prompt: str = Field(default="", alias="DEFAULT_SYSTEM_PROMPT")
    max_history_messages: int = Field(default=6, alias="MAX_HISTORY_MESSAGES")
    claude_max_tokens: int = Field(default=512, alias="CLAUDE_MAX_TOKENS")

    web_access_token: str = Field(default="change_me", alias="WEB_ACCESS_TOKEN")
    web_owner_id: int = Field(default=1, alias="WEB_OWNER_ID")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
