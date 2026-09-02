from __future__ import annotations

import logging
from dataclasses import dataclass

from chief_reception_demo.database.repositories import BookingRepository, BookingRequest
from chief_reception_demo.services.availability_service import AvailabilityService

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BookingDraft:
    telegram_user_id: int
    telegram_username: str | None
    telegram_first_name: str | None
    client_name: str
    phone: str | None
    selected_service: str
    selected_date: str
    selected_time: str


class BookingService:
    def __init__(
        self,
        *,
        bookings: BookingRepository,
        availability: AvailabilityService,
    ) -> None:
        self.bookings = bookings
        self.availability = availability

    def create_booking(self, draft: BookingDraft) -> BookingRequest:
        selected_date = self._storage_date(draft.selected_date)
        logger.info(
            "[BOOKING] Persisting booking user_id=%s service=%s date=%s time=%s",
            draft.telegram_user_id,
            draft.selected_service,
            selected_date,
            draft.selected_time,
        )
        booking = self.bookings.create(
            telegram_user_id=draft.telegram_user_id,
            telegram_username=draft.telegram_username,
            telegram_first_name=draft.telegram_first_name,
            client_name=draft.client_name,
            phone=draft.phone,
            selected_service=draft.selected_service,
            selected_date=selected_date,
            selected_time=draft.selected_time,
        )
        logger.info("[BOOKING] Persisted booking_id=%s", booking.booking_id)
        return booking

    def _storage_date(self, selected_date: str) -> str:
        try:
            return self.availability.format_date(selected_date)
        except ValueError:
            return selected_date
