from __future__ import annotations

import re
from urllib.parse import quote_plus

import aiohttp

from src.infrastructure.logging.logger import get_logger
from src.services.web_search import SerperSearchService, WebSearchUnavailable, format_search_context

logger = get_logger(__name__)


LIVE_INFO_KEYWORDS = (
    "погода",
    "прогноз",
    "новости",
    "сейчас",
    "сегодня",
    "завтра",
    "последние",
    "актуальн",
    "население",
    "расписание",
    "курс",
    "цена",
    "стоимость",
    "события",
    "время",
    "который час",
    "weather",
    "forecast",
    "news",
    "latest",
    "population",
    "schedule",
    "price",
    "current time",
    "time in",
)

CURRENT_WEATHER_KEYWORDS = ("сейчас", "прямо сейчас", "текущ", "что сейчас", "какая сейчас", "now", "current")
FORECAST_KEYWORDS = ("завтра", "послезавтра", "через", "прогноз", "на неделю", "forecast", "tomorrow")
TIME_KEYWORDS = ("сколько времени", "который час", "время в", "time in", "current time")


def wants_live_info(text: str) -> bool:
    low = (text or "").lower()
    return (
        any(keyword in low for keyword in LIVE_INFO_KEYWORDS)
        or _is_time_request(text)
        or _is_current_weather_request(text)
        or _is_forecast_request(text)
    )


def _is_current_weather_request(text: str) -> bool:
    low = (text or "").lower()
    return ("погод" in low or "weather" in low) and any(keyword in low for keyword in CURRENT_WEATHER_KEYWORDS)


def _is_forecast_request(text: str) -> bool:
    low = (text or "").lower()
    return ("погод" in low or "weather" in low) and any(keyword in low for keyword in FORECAST_KEYWORDS)


def _is_time_request(text: str) -> bool:
    low = (text or "").lower()
    return any(keyword in low for keyword in TIME_KEYWORDS)


def _clean_location(location: str) -> str | None:
    value = re.sub(r"[?.!,]+$", "", location or "").strip()
    value = re.sub(
        r"\b(сейчас|сегодня|завтра|послезавтра|now|current|today|tomorrow|forecast|прогноз)\b",
        "",
        value,
        flags=re.IGNORECASE,
    ).strip()
    return value[:80] or None


def _weather_location(text: str) -> str | None:
    patterns = [
        r"(?:прогноз\s+)?погод[аы]?(?:\s+\w+){0,3}?\s+(?:в|во|на)\s+(.+)",
        r"(?:в|во|на)\s+(.+?)\s+(?:сейчас|сегодня|завтра|послезавтра|now|today|tomorrow)\b",
        r"(?:какая|что)\s+сейчас\s+по\s+погоде\s+(?:в|во|на)\s+(.+)",
        r"(?:какая\s+сейчас\s+погода|погода\s+сейчас)\s+(?:в|во|на)\s+(.+)",
        r"weather\s+(?:now\s+)?(?:in\s+)?(.+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            location = _clean_location(match.group(1))
            if location:
                return location
    return None


def _time_location(text: str) -> str | None:
    patterns = [
        r"(?:сколько времени|который час|время)\s+(?:в|во|на)\s+(.+)",
        r"(?:time in|current time in)\s+(.+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return _clean_location(match.group(1))
    return None


class LiveInfoService:
    def __init__(self, timeout_seconds: float = 15.0) -> None:
        self.timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self.search = SerperSearchService(timeout_seconds=timeout_seconds, max_results=5)

    async def context_for(self, text: str) -> str:
        if not wants_live_info(text):
            return ""

        snippets: list[str] = []
        search_failed = False
        search_error = ""
        time_location = _time_location(text) if _is_time_request(text) else None
        weather_location = _weather_location(text)

        if time_location:
            time_context = await self._city_time(time_location)
            if time_context:
                snippets.append(time_context)

        if weather_location and _is_current_weather_request(text):
            weather = await self._current_weather(weather_location)
            if weather:
                snippets.append(weather)

        search_query = self._build_search_query(text, weather_location, time_location)
        try:
            search_context = await self.search.search_context(search_query)
            formatted = format_search_context(search_context)
            if formatted:
                snippets.append(formatted)
        except WebSearchUnavailable as error:
            search_failed = True
            search_error = str(error)
            logger.warning("[WEB_SEARCH] live context unavailable", query=search_query, error=search_error)

        if search_failed and not snippets:
            return f"Web search context: search is currently unavailable. Error: {search_error}"
        return "\n\n".join(snippets).strip()

    def _build_search_query(self, text: str, weather_location: str | None, time_location: str | None) -> str:
        if weather_location and _is_current_weather_request(text):
            return f"current weather now in {weather_location}"
        if weather_location and _is_forecast_request(text):
            return f"weather forecast {weather_location} {text}"
        if time_location:
            return f"current time in {time_location}"
        return text

    async def _current_weather(self, location: str) -> str:
        url = f"https://wttr.in/{quote_plus(location)}?format=j1"
        try:
            async with aiohttp.ClientSession(timeout=self.timeout, trust_env=True) as session:
                async with session.get(url) as response:
                    if response.status != 200:
                        logger.warning("current weather lookup failed", status=response.status, location=location)
                        return ""
                    data = await response.json(content_type=None)
        except Exception as error:
            logger.warning("current weather lookup unavailable", location=location, error=str(error))
            return ""

        current = (data.get("current_condition") or [{}])[0]
        temp = current.get("temp_C")
        feels = current.get("FeelsLikeC")
        desc = ((current.get("weatherDesc") or [{}])[0].get("value") or "").strip()
        humidity = current.get("humidity")
        wind = current.get("windspeedKmph")
        observation = current.get("localObsDateTime") or current.get("observation_time")
        if not temp:
            return ""
        return (
            "Current weather NOW context:\n"
            f"Location: {location}\n"
            f"Observed at: {observation or 'current endpoint'}\n"
            f"Temperature: {temp}C\n"
            f"Feels like: {feels}C\n"
            f"Conditions: {desc}\n"
            f"Humidity: {humidity}%\n"
            f"Wind: {wind} km/h\n"
            "Use this as current conditions, not as daily average forecast."
        )

    async def _city_time(self, location: str) -> str:
        try:
            search_context = await self.search.search_context(f"current time in {location}")
        except WebSearchUnavailable as error:
            logger.warning("city time lookup unavailable", location=location, error=str(error))
            return ""

        lines = ["City local time context:", f"Location query: {location}"]
        if search_context.answer:
            lines.append(f"Current local time answer: {search_context.answer}")
        if search_context.answer_title:
            lines.append(f"Answer title: {search_context.answer_title}")
        if search_context.answer_source:
            lines.append(f"Source: {search_context.answer_source}")
        if not search_context.answer and search_context.results:
            first = search_context.results[0]
            lines.append(f"Best snippet: {first.title} - {first.snippet}")
        lines.append("Use this city-local time when answering time-in-city questions.")
        return "\n".join(lines)
