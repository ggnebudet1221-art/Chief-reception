from __future__ import annotations

import html
import logging

from aiogram import Bot

from chief_reception_demo.core.config import Settings
from chief_reception_demo.database.repositories import BookingRequest

logger = logging.getLogger(__name__)


class NotificationService:
    def __init__(self, *, bot: Bot, settings: Settings) -> None:
        self.bot = bot
        self.settings = settings

    async def notify_owner(self, booking: BookingRequest) -> None:
        logger.info("[NOTIFICATION] Sending booking_id=%s to owner", booking.booking_id)
        phone = booking.phone or "not provided"
        text = (
            "<b>Новая запись</b>\n\n"
            f"Услуга: {html.escape(booking.selected_service)}\n"
            f"Дата: {html.escape(booking.selected_date)}\n"
            f"Время: {html.escape(booking.selected_time or '')}\n"
            f"Имя: {html.escape(booking.client_name)}\n"
            f"Телефон: {html.escape(phone)}\n"
            f"Telegram ID: {booking.telegram_user_id}"
        )
        await self.bot.send_message(self.settings.owner_telegram_id, text, parse_mode="HTML")
        logger.info("[NOTIFICATION] Sent booking_id=%s to owner", booking.booking_id)

    async def notify_owner_cancelled(self, booking: BookingRequest) -> None:
        logger.info("[NOTIFICATION] Sending cancellation booking_id=%s to owner", booking.booking_id)
        text = (
            "<b>Запись отменена</b>\n\n"
            f"Услуга: {html.escape(booking.selected_service)}\n"
            f"Дата: {html.escape(booking.selected_date)}\n"
            f"Время: {html.escape(booking.selected_time or '')}\n"
            f"Имя: {html.escape(booking.client_name)}\n"
            f"Телефон: {html.escape(booking.phone or 'not provided')}\n"
            f"Telegram ID: {booking.telegram_user_id}"
        )
        await self.bot.send_message(self.settings.owner_telegram_id, text, parse_mode="HTML")
        logger.info("[NOTIFICATION] Sent cancellation booking_id=%s to owner", booking.booking_id)

    async def notify_owner_rescheduled(self, booking: BookingRequest) -> None:
        logger.info("[NOTIFICATION] Sending reschedule booking_id=%s to owner", booking.booking_id)
        text = (
            "<b>Запись перенесена</b>\n\n"
            f"Услуга: {html.escape(booking.selected_service)}\n"
            f"Новая дата: {html.escape(booking.selected_date)}\n"
            f"Новое время: {html.escape(booking.selected_time or '')}\n"
            f"Имя: {html.escape(booking.client_name)}\n"
            f"Телефон: {html.escape(booking.phone or 'not provided')}\n"
            f"Telegram ID: {booking.telegram_user_id}"
        )
        await self.bot.send_message(self.settings.owner_telegram_id, text, parse_mode="HTML")
        logger.info("[NOTIFICATION] Sent reschedule booking_id=%s to owner", booking.booking_id)

    async def notify_owner_client_late(
        self,
        *,
        booking: BookingRequest,
        old_time: str,
        new_time: str,
        delay_minutes: int,
    ) -> None:
        logger.info("[NOTIFICATION] Sending late notice booking_id=%s to owner", booking.booking_id)
        text = (
            "<b>Клиент опаздывает</b>\n\n"
            f"Имя: {html.escape(booking.client_name)}\n"
            f"Услуга: {html.escape(booking.selected_service)}\n"
            f"Дата: {html.escape(booking.selected_date)}\n"
            f"Старая запись: {html.escape(old_time)}\n"
            f"Новое время: {html.escape(new_time)}\n"
            f"Опоздание: {delay_minutes} мин\n"
            f"Telegram ID: {booking.telegram_user_id}"
        )
        await self.bot.send_message(self.settings.owner_telegram_id, text, parse_mode="HTML")
        logger.info("[NOTIFICATION] Sent late notice booking_id=%s to owner", booking.booking_id)

    async def notify_owner_client_question(
        self,
        *,
        telegram_user_id: int,
        client_name: str | None,
        question_text: str,
    ) -> None:
        logger.info("[NOTIFICATION] Sending client question user_id=%s to owner", telegram_user_id)
        text = (
            "⚠️ <b>Новый вопрос клиента</b>\n\n"
            f"Имя: {html.escape(client_name or 'неизвестно')}\n\n"
            f"Telegram ID: {telegram_user_id}\n\n"
            "Вопрос клиента:\n"
            f"{html.escape(question_text)}\n\n"
            "Требуется ответ администратора."
        )
        await self.bot.send_message(self.settings.owner_telegram_id, text, parse_mode="HTML")
        logger.info("[NOTIFICATION] Sent client question user_id=%s to owner", telegram_user_id)
