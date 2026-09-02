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
    scout_anthropic_api_key: str = Field(default="", alias="SCOUT_ANTHROPIC_API_KEY")
    scout_anthropic_base_url: str = Field(default="", alias="SCOUT_ANTHROPIC_BASE_URL")
    scout_anthropic_model: str = Field(default="", alias="SCOUT_ANTHROPIC_MODEL")

    database_url: str = Field(default="sqlite+aiosqlite:///./ai_manager.db", alias="DATABASE_URL")
    timezone: str = Field(default="Asia/Yekaterinburg", alias="TIMEZONE")

    default_system_prompt: str = Field(default="", alias="DEFAULT_SYSTEM_PROMPT")
    max_history_messages: int = Field(default=6, alias="MAX_HISTORY_MESSAGES")
    claude_max_tokens: int = Field(default=512, alias="CLAUDE_MAX_TOKENS")
    serper_api_key: str = Field(default="", alias="SERPER_API_KEY")
    yandex_geocoder_api_key: str = Field(default="", alias="YANDEX_GEOCODER_API_KEY")
    yandex_routing_api_key: str = Field(default="", alias="YANDEX_ROUTING_API_KEY")
    yandex_maps_default_origin: str = Field(default="", alias="YANDEX_MAPS_DEFAULT_ORIGIN")

    web_access_token: str = Field(default="change_me", alias="WEB_ACCESS_TOKEN")
    web_owner_id: int = Field(default=1, alias="WEB_OWNER_ID")

    telegram_bot_token: str = Field(default="", alias="TELEGRAM_BOT_TOKEN")
    chief_bot_token: str = Field(default="", alias="CHIEF_BOT_TOKEN")
    business_bot_token: str = Field(default="", alias="BUSINESS_BOT_TOKEN")
    smm_bot_token: str = Field(default="", alias="SMM_BOT_TOKEN")
    chief_agent_name: str = Field(default="Chief", alias="CHIEF_AGENT_NAME")
    business_agent_name: str = Field(default="Business", alias="BUSINESS_AGENT_NAME")
    smm_agent_name: str = Field(default="SMM", alias="SMM_AGENT_NAME")
    chief_prompt_path: str = Field(default="prompts/chief.txt", alias="CHIEF_PROMPT_PATH")
    business_prompt_path: str = Field(default="prompts/business.txt", alias="BUSINESS_PROMPT_PATH")
    smm_prompt_path: str = Field(default="prompts/smm.txt", alias="SMM_PROMPT_PATH")
    owner_telegram_id: int = Field(default=0, alias="OWNER_TELEGRAM_ID")
    enable_telegram_bot: bool = Field(default=True, alias="ENABLE_TELEGRAM_BOT")
    telegram_request_timeout: int = Field(default=45, alias="TELEGRAM_REQUEST_TIMEOUT")
    telegram_polling_timeout: int = Field(default=30, alias="TELEGRAM_POLLING_TIMEOUT")
    agent_task_timeout_seconds: int = Field(default=60, alias="AGENT_TASK_TIMEOUT_SECONDS")
    telegram_proxy_url: str = Field(default="", alias="TELEGRAM_PROXY_URL")
    telegram_chief_chat_id: int = Field(default=0, alias="TELEGRAM_CHIEF_CHAT_ID")
    telegram_business_chat_id: int = Field(default=0, alias="TELEGRAM_BUSINESS_CHAT_ID")
    telegram_smm_chat_id: int = Field(default=0, alias="TELEGRAM_SMM_CHAT_ID")
    telegram_coordination_chat_id: int = Field(default=0, alias="TELEGRAM_COORDINATION_CHAT_ID")
    telegram_general_topic_id: int = Field(default=0, alias="TELEGRAM_GENERAL_TOPIC_ID")
    telegram_tasks_topic_id: int = Field(default=0, alias="TELEGRAM_TASKS_TOPIC_ID")
    telegram_infra_topic_id: int = Field(default=0, alias="TELEGRAM_INFRA_TOPIC_ID")
    enable_proactive_chief: bool = Field(default=True, alias="ENABLE_PROACTIVE_CHIEF")
    morning_brief_time: str = Field(default="08:00", alias="MORNING_BRIEF_TIME")
    evening_reflection_time: str = Field(default="22:00", alias="EVENING_REFLECTION_TIME")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
