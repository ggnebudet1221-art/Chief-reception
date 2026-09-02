from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from chief_reception_demo.bot.dialog import create_router
from chief_reception_demo.core.config import load_settings
from chief_reception_demo.database.repositories import BookingRepository, ClientQuestionRepository, ServiceRepository
from chief_reception_demo.database.sqlite import connect
from chief_reception_demo.services.availability_service import AvailabilityService
from chief_reception_demo.services.booking_service import BookingService
from chief_reception_demo.services.catalog import DEFAULT_SERVICES
from chief_reception_demo.services.claude_client import AnthropicReceptionistClient
from chief_reception_demo.services.extraction_service import ExtractionService
from chief_reception_demo.services.notification_service import NotificationService
from chief_reception_demo.services.receptionist_service import ReceptionistService


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = load_settings()

    connection = connect(settings.database_path)
    service_repository = ServiceRepository(connection)
    service_repository.seed_defaults(DEFAULT_SERVICES)
    booking_repository = BookingRepository(connection)
    question_repository = ClientQuestionRepository(connection)
    availability_service = AvailabilityService(settings.timezone)

    bot = Bot(token=settings.telegram_bot_token)
    notification_service = NotificationService(bot=bot, settings=settings)
    claude_client = AnthropicReceptionistClient(
        api_key=settings.anthropic_api_key,
        base_url=settings.anthropic_base_url,
        model=settings.anthropic_model,
        max_tokens=settings.claude_max_tokens,
    )
    extraction_service = ExtractionService(
        claude=claude_client,
        services=service_repository,
        availability=availability_service,
        salon_name=settings.salon_name,
    )
    booking_service = BookingService(
        bookings=booking_repository,
        availability=availability_service,
    )
    receptionist_service = ReceptionistService(
        services=service_repository,
        availability=availability_service,
        booking_service=booking_service,
        notification_service=notification_service,
        extraction_service=extraction_service,
        question_repository=question_repository,
        salon_name=settings.salon_name,
    )

    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.include_router(create_router(receptionist=receptionist_service))

    logging.info("Chief Reception demo bot started with isolated database: %s", settings.database_path)
    await dispatcher.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
