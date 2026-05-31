from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

from src.core.config import get_settings
from src.infrastructure.db.models.memory import Reminder
from src.infrastructure.db.session import AsyncSessionLocal
from src.infrastructure.logging.logger import get_logger

logger = get_logger(__name__)


class ReminderSchedulerService:
    def __init__(self, bot: Bot) -> None:
        settings = get_settings()
        self._bot = bot
        self._scheduler = AsyncIOScheduler(timezone=ZoneInfo(settings.timezone))

    async def start(self) -> None:
        logger.info("Scheduler starting")
        self._scheduler.start()
        await self._restore_jobs()
        logger.info("Scheduler started")

    async def shutdown(self) -> None:
        logger.info("Scheduler shutdown")
        self._scheduler.shutdown(wait=False)

    async def _restore_jobs(self) -> None:
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(Reminder).where(Reminder.status == "active"))
            reminders = result.scalars().all()
        logger.info("Restoring reminders", count=len(reminders))
        now = datetime.now(ZoneInfo(get_settings().timezone))
        for reminder in reminders:
            if reminder.remind_at <= now:
                logger.info("Reminder in the past; executing now", reminder_id=reminder.id)
                self.schedule_reminder(reminder.id, now)
            else:
                self.schedule_reminder(reminder.id, reminder.remind_at)

    def schedule_reminder(self, reminder_id: int, remind_at: datetime) -> None:
        logger.info("Scheduling reminder", reminder_id=reminder_id, remind_at=str(remind_at))
        self._scheduler.add_job(
            self._run_reminder,
            "date",
            run_date=remind_at,
            id=f"reminder:{reminder_id}",
            replace_existing=True,
            kwargs={"reminder_id": reminder_id},
        )

    async def _run_reminder(self, reminder_id: int) -> None:
        logger.info("Reminder due", reminder_id=reminder_id)
        try:
            async with AsyncSessionLocal() as session:
                reminder = await session.get(Reminder, reminder_id)
                if reminder is None:
                    logger.info("Reminder skipped", reason="not_found", reminder_id=reminder_id)
                    return
                if reminder.status != "active":
                    logger.info("Reminder skipped", reason="not_active", status=reminder.status, reminder_id=reminder_id)
                    return
                chat_id = reminder.chat_id if reminder.chat_id is not None else get_settings().owner_telegram_id
                if chat_id is None:
                    logger.info("Reminder skipped", reason="chat_id_missing", reminder_id=reminder_id, user_id=reminder.user_id)
                    return
                await self._bot.send_message(chat_id, f"⏰ Напоминание: {reminder.text}")
                logger.info("Reminder telegram message sent", reminder_id=reminder_id, chat_id=chat_id)
                reminder.status = "done"
                reminder.completed_at = datetime.now(timezone.utc)
                await session.commit()
                logger.info("Reminder completed", reminder_id=reminder_id)
        except Exception as exc:
            logger.exception("Reminder scheduler error", error=str(exc), reminder_id=reminder_id)
