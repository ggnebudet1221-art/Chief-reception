from __future__ import annotations

from chief_reception_demo.services.availability_service import AVAILABLE_TIMES, AvailabilityService


def format_available_times(timezone: str, days: int = 5) -> str:
    return AvailabilityService(timezone).format_available_times(days)


def parse_date(text: str, timezone: str) -> str | None:
    return AvailabilityService(timezone).parse_date(text)


def parse_time(text: str) -> str | None:
    return AvailabilityService("UTC").parse_time(text)
