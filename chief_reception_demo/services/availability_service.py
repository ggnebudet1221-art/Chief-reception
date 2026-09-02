from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta, timezone
from datetime import timezone as datetime_timezone
from typing import Final
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger(__name__)

DEFAULT_TIMEZONE = "UTC"
SUPPORTED_TIMEZONES = {"UTC", "Europe/Moscow"}
MOSCOW_TIMEZONE: Final = timezone(timedelta(hours=3), name="Europe/Moscow")
AVAILABLE_TIMES = ["10:00", "12:00", "14:00", "15:00", "16:00", "18:00", "19:30"]
WEEKDAYS = {
    "понедельник": 0,
    "вторник": 1,
    "среда": 2,
    "среду": 2,
    "четверг": 3,
    "пятница": 4,
    "пятницу": 4,
    "суббота": 5,
    "субботу": 5,
    "воскресенье": 6,
}
WEEKDAY_NAMES = {
    0: "понедельник",
    1: "вторник",
    2: "среда",
    3: "четверг",
    4: "пятница",
    5: "суббота",
    6: "воскресенье",
}
MONTHS = {
    "января": 1,
    "январь": 1,
    "февраля": 2,
    "февраль": 2,
    "марта": 3,
    "март": 3,
    "апреля": 4,
    "апрель": 4,
    "мая": 5,
    "май": 5,
    "июня": 6,
    "июнь": 6,
    "июля": 7,
    "июль": 7,
    "августа": 8,
    "август": 8,
    "сентября": 9,
    "сентябрь": 9,
    "октября": 10,
    "октябрь": 10,
    "ноября": 11,
    "ноябрь": 11,
    "декабря": 12,
    "декабрь": 12,
}
TIME_WORDS = {
    "десять": 10,
    "одиннадцать": 11,
    "двенадцать": 12,
    "час": 13,
    "два": 14,
    "три": 15,
    "четыре": 16,
    "пять": 17,
    "шесть": 18,
    "шести": 18,
    "семь": 19,
    "семи": 19,
}


def resolve_timezone(timezone_key: str):
    if timezone_key == "UTC":
        return datetime_timezone.utc
    if timezone_key == "Europe/Moscow":
        return MOSCOW_TIMEZONE

    try:
        return ZoneInfo(timezone_key)
    except ZoneInfoNotFoundError:
        logger.warning("Invalid timezone %r. Falling back to UTC.", timezone_key)
        return datetime_timezone.utc


class AvailabilityService:
    def __init__(self, timezone: str) -> None:
        self.timezone_key = timezone
        self.timezone = resolve_timezone(timezone)

    def today(self) -> date:
        return datetime.now(self.timezone).date()

    def now(self) -> datetime:
        return datetime.now(self.timezone)

    def current_context(self) -> dict[str, str]:
        current = self.now()
        return {
            "date": current.strftime("%d.%m.%Y"),
            "weekday": WEEKDAY_NAMES[current.weekday()],
            "timezone": self.timezone_key,
        }

    def available_times(self) -> list[str]:
        return AVAILABLE_TIMES.copy()

    def available_times_for_date(self, selected_date: str | None = None) -> list[str]:
        return self.available_times()

    def available_times_for_period(self, period: str | None) -> list[str]:
        if period == "evening":
            return [time for time in AVAILABLE_TIMES if time >= "18:00"]
        if period == "afternoon":
            return [time for time in AVAILABLE_TIMES if "12:00" <= time < "18:00"]
        if period == "morning":
            return [time for time in AVAILABLE_TIMES if time < "12:00"]
        return self.available_times()

    def is_time_available(self, selected_time: str) -> bool:
        return selected_time in AVAILABLE_TIMES

    def alternative_times(self, selected_time: str, *, period: str | None = None) -> list[str]:
        times = self.available_times_for_period(period)
        requested_minutes = _minutes(selected_time)
        ordered = sorted(
            [time for time in times if time != selected_time],
            key=lambda value: abs(_minutes(value) - requested_minutes),
        )
        return sorted(ordered[:2]) if ordered else self.available_times()[:2]

    def format_available_times(self, days: int = 5) -> str:
        current = self.today()
        lines = ["Ближайшие свободные окна:"]
        for offset in range(days):
            day = current + timedelta(days=offset)
            lines.append(f"{day.strftime('%d.%m')}: {', '.join(AVAILABLE_TIMES)}")
        return "\n".join(lines)

    def format_times_for_period(self, period: str | None) -> str:
        return ", ".join(self.available_times_for_period(period))

    def parse_date(self, text: str, *, allow_bare_day: bool = True) -> str | None:
        normalized = text.casefold().strip()
        base = self.today()
        parsed_date: str | None = None

        if (
            "послезавтра" in normalized
            or "после завтра" in normalized
            or "через два дня" in normalized
            or re.search(r"\bчерез\s+2\s+дн", normalized)
        ):
            parsed_date = (base + timedelta(days=2)).isoformat()
        elif "завтра" in normalized:
            parsed_date = (base + timedelta(days=1)).isoformat()
        elif "сегодня" in normalized:
            parsed_date = base.isoformat()
        elif "через неделю" in normalized:
            parsed_date = (base + timedelta(days=7)).isoformat()
        elif any(phrase in normalized for phrase in ("на выходных", "в выходные", "выходные")):
            days_ahead = (5 - base.weekday()) % 7
            if days_ahead == 0 and base.weekday() in (5, 6):
                days_ahead = 0
            elif days_ahead == 0:
                days_ahead = 7
            parsed_date = (base + timedelta(days=days_ahead)).isoformat()

        if parsed_date:
            return self._log_parsed_date(text, parsed_date)

        for word, weekday in WEEKDAYS.items():
            if re.search(rf"\b(?:в\s+|на\s+)?{re.escape(word)}\b", normalized):
                days_ahead = (weekday - base.weekday()) % 7
                if days_ahead == 0:
                    days_ahead = 7
                parsed_date = (base + timedelta(days=days_ahead)).isoformat()
                return self._log_parsed_date(text, parsed_date)

        month_match = re.search(
            r"\b(?:на\s+)?(\d{1,2})(?:-?е|ое|го)?\s+([а-яё]+)\b",
            normalized,
        )
        if month_match:
            day = int(month_match.group(1))
            month = MONTHS.get(month_match.group(2))
            if month:
                parsed_date = self._date_from_day_month(day, month, base)
                return self._log_parsed_date(text, parsed_date)

        day_of_month = re.search(r"\b(?:на\s+)?(\d{1,2})(?:-?е|ое|го)?\s+числ[оа]\b", normalized)
        if day_of_month:
            day = int(day_of_month.group(1))
            parsed_date = self._date_from_day_month(day, base.month, base)
            return self._log_parsed_date(text, parsed_date)

        if allow_bare_day:
            bare_day = re.fullmatch(r"(?:на\s+)?(\d{1,2})", normalized)
            if bare_day:
                day = int(bare_day.group(1))
                parsed_date = self._date_from_day_month(day, base.month, base)
                return self._log_parsed_date(text, parsed_date)

        match = re.search(r"\b(\d{1,2})[./-](\d{1,2})(?:[./-](\d{2,4}))?\b", normalized)
        if not match:
            return self._log_parsed_date(text, None)

        day = int(match.group(1))
        month = int(match.group(2))
        year = int(match.group(3)) if match.group(3) else base.year
        if year < 100:
            year += 2000

        try:
            parsed = date(year, month, day)
        except ValueError:
            return self._log_parsed_date(text, None)

        if parsed < base and not match.group(3):
            parsed = date(base.year + 1, month, day)
        return self._log_parsed_date(text, parsed.isoformat())

    def _date_from_day_month(self, day: int, month: int, base: date) -> str | None:
        try:
            parsed = date(base.year, month, day)
        except ValueError:
            return None
        if parsed < base:
            try:
                parsed = date(base.year + 1, month, day)
            except ValueError:
                return None
        return parsed.isoformat()

    def _log_parsed_date(self, user_text: str, parsed_date: str | None) -> str | None:
        logger.info('[DATE PARSER] user_text="%s" parsed_date="%s"', user_text, parsed_date or "")
        return parsed_date

    def parse_time(self, text: str) -> str | None:
        candidate = self.parse_requested_time(text)
        return candidate if candidate in AVAILABLE_TIMES else None

    def parse_requested_time(self, text: str) -> str | None:
        normalized = text.casefold()
        match = re.search(r"\b(?:к|на|в)?\s*([01]?\d|2[0-3])(?::|\.|\s)?([0-5]\d)?\b", normalized)
        if not match:
            for word, hour in TIME_WORDS.items():
                if word in normalized:
                    return f"{hour:02d}:00"
            if "после работы" in normalized:
                return "18:00"
            return None

        hour = int(match.group(1))
        minute = int(match.group(2) or 0)
        if hour < 8 and any(word in normalized for word in ("вечер", "после работы", "районе")):
            hour += 12
        return f"{hour:02d}:{minute:02d}"

    def parse_period(self, text: str) -> str | None:
        normalized = text.casefold()
        if any(word in normalized for word in ("вечер", "после работы")):
            return "evening"
        if any(word in normalized for word in ("днем", "день", "обед")):
            return "afternoon"
        if any(word in normalized for word in ("утро", "утром")):
            return "morning"
        return None

    def combine_date_time(self, selected_date: str, selected_time: str) -> str:
        return f"{self.format_date(selected_date)} {selected_time}"

    def format_date(self, selected_date: str) -> str:
        day = datetime.strptime(selected_date, "%Y-%m-%d").date()
        return day.strftime("%d.%m.%Y")


def _minutes(value: str) -> int:
    hour, minute = value.split(":", 1)
    return int(hour) * 60 + int(minute)
