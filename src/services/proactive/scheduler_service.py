from __future__ import annotations

from zoneinfo import ZoneInfo

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from src.core.config import get_settings
from src.infrastructure.logging.logger import get_logger
from src.services.proactive.daily_briefing import DailyBriefingService

logger = get_logger(__name__)


def _parse_hhmm(value: str, fallback: str) -> tuple[int, int]:
    raw = (value or fallback).strip()
    try:
        hour_text, minute_text = raw.split(":", 1)
        hour = int(hour_text)
        minute = int(minute_text)
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return hour, minute
    except (TypeError, ValueError):
        pass
    logger.warning("Invalid proactive schedule time; using fallback", value=value, fallback=fallback)
    return _parse_hhmm(fallback, "08:00") if value != fallback else (8, 0)


class ProactiveChiefSchedulerService:
    def __init__(self, bot: Bot) -> None:
        settings = get_settings()
        self._bot = bot
        self._service = DailyBriefingService()
        self._scheduler = AsyncIOScheduler(timezone=ZoneInfo(settings.timezone))

    async def start(self) -> None:
        settings = get_settings()
        if not settings.enable_proactive_chief:
            logger.info("Proactive Chief scheduler disabled")
            return

        morning_hour, morning_minute = _parse_hhmm(settings.morning_brief_time, "08:00")
        evening_hour, evening_minute = _parse_hhmm(settings.evening_reflection_time, "22:00")
        self._scheduler.add_job(
            self._service.run_morning_brief,
            "cron",
            hour=morning_hour,
            minute=morning_minute,
            id="chief:morning_brief",
            replace_existing=True,
            kwargs={"bot": self._bot},
        )
        self._scheduler.add_job(
            self._service.run_evening_reflection,
            "cron",
            hour=evening_hour,
            minute=evening_minute,
            id="chief:evening_reflection",
            replace_existing=True,
            kwargs={"bot": self._bot},
        )
        logger.info(
            "Proactive Chief scheduler starting",
            timezone=settings.timezone,
            morning=settings.morning_brief_time,
            evening=settings.evening_reflection_time,
        )
        self._scheduler.start()
        logger.info("Proactive Chief scheduler started")

    async def shutdown(self) -> None:
        logger.info("Proactive Chief scheduler shutdown")
        self._scheduler.shutdown(wait=False)
