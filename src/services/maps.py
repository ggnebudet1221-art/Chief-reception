from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

import aiohttp

from src.core.config import get_settings
from src.infrastructure.logging.logger import get_logger

logger = get_logger(__name__)


MAPS_KEYWORDS = (
    "адрес",
    "аэропорт",
    "ближайш",
    "добраться",
    "доехать",
    "дорог",
    "ехать",
    "зал",
    "иди",
    "идти",
    "км",
    "километр",
    "маршрут",
    "место",
    "пешком",
    "пробк",
    "расстояние",
    "сколько ехать",
    "сколько идти",
    "такси",
    "тц",
    "торговый центр",
    "успею",
    "mall",
    "maps",
    "route",
    "traffic",
    "walk",
)


@dataclass(frozen=True)
class GeoPoint:
    query: str
    title: str
    address: str
    lon: float
    lat: float


@dataclass(frozen=True)
class RouteRequest:
    origin: str
    destination: str
    modes: tuple[str, ...]


@dataclass(frozen=True)
class RouteSummary:
    mode: str
    distance_m: int | None
    duration_s: int | None
    traffic_duration_s: int | None = None
    traffic_type: str = ""


def wants_maps_info(text: str) -> bool:
    low = (text or "").lower()
    return any(keyword in low for keyword in MAPS_KEYWORDS)


def parse_route_request(text: str) -> RouteRequest | None:
    clean = re.sub(r"\s+", " ", text or "").strip()
    if not clean:
        return None

    modes = _detect_modes(clean)
    patterns = (
        r"(?:от|из)\s+(.+?)\s+(?:до|в)\s+(.+?)(?:[?.!,]|$)",
        r"from\s+(.+?)\s+to\s+(.+?)(?:[?.!,]|$)",
    )
    for pattern in patterns:
        match = re.search(pattern, clean, flags=re.IGNORECASE)
        if match:
            origin = _clean_place(match.group(1))
            destination = _clean_place(match.group(2))
            if origin and destination:
                return RouteRequest(origin=origin, destination=destination, modes=modes)

    origin = _extract_origin(clean)
    destination = _extract_destination(clean)
    if origin and destination:
        origin = _add_city_hint(origin, destination)
        destination = _normalize_destination(destination)
        return RouteRequest(origin=origin, destination=destination, modes=modes)

    if not destination:
        return None

    settings = get_settings()
    origin = settings.yandex_maps_default_origin.strip()
    destination = _normalize_destination(destination)
    return RouteRequest(origin=origin, destination=destination, modes=modes)


def _detect_modes(text: str) -> tuple[str, ...]:
    low = text.lower()
    wants_walk = any(word in low for word in ("пешком", "идти", "дойти", "walk"))
    wants_drive = any(word in low for word in ("ехать", "доехать", "машин", "такси", "пробк", "drive", "traffic"))
    if wants_walk and wants_drive:
        return ("driving", "walking")
    if wants_walk:
        return ("walking",)
    if "как быстрее" in low or "быстрее добраться" in low:
        return ("driving", "walking")
    return ("driving",)


def _extract_destination(text: str) -> str:
    patterns = (
        r"в\s+(центр[е]?\s+[А-Яа-яA-Za-zЁё-]+)",
        r"в\s+центре\s+([А-Яа-яA-Za-zЁё-]+)",
        r"(?:до|в)\s+(.+?)(?:[?.!,]|$)",
        r"(?:найди|найти|ищи|покажи)\s+(?:ближайший|ближайшую|ближайшее|ближайшие)?\s*(.+?)(?:[?.!,]|$)",
        r"(?:nearest|find)\s+(.+?)(?:[?.!,]|$)",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            destination = _clean_place(match.group(1))
            destination = re.sub(r"^центре\s+", "центр ", destination, flags=re.IGNORECASE)
            if pattern.startswith("в\\s+центре") and destination:
                destination = f"центр {destination}"
            if destination:
                return destination
    return ""


def _extract_origin(text: str) -> str:
    patterns = (
        r"(?:от|из)\s+(.+?)(?:\s+и\s+|[?.!,]|$)",
        r"from\s+(.+?)(?:\s+and\s+|[?.!,]|$)",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            origin = _clean_place(match.group(1))
            if origin:
                return origin
    return ""


def _add_city_hint(origin: str, destination: str) -> str:
    if re.search(r"\bуф", destination, flags=re.IGNORECASE) and not re.search(r"\bуф", origin, flags=re.IGNORECASE):
        return f"Уфа, {origin}"
    return origin


def _normalize_destination(destination: str) -> str:
    if re.search(r"^центр\s+уф", destination, flags=re.IGNORECASE):
        return "Уфа, центр"
    return destination


def _clean_place(value: str) -> str:
    value = re.sub(
        r"\b(сколько|ехать|идти|пешком|на машине|на такси|быстрее|добраться|успею|за\s+\d+\s+минут?)\b",
        " ",
        value or "",
        flags=re.IGNORECASE,
    )
    value = re.sub(r"\s+", " ", value).strip(" ,.!?;:-")
    return value[:140]


def _format_duration(seconds: int | None) -> str:
    if seconds is None:
        return "нет оценки"
    minutes = max(1, round(seconds / 60))
    if minutes < 60:
        return f"~{minutes} мин"
    hours = minutes // 60
    rest = minutes % 60
    if rest:
        return f"~{hours} ч {rest} мин"
    return f"~{hours} ч"


def _format_distance(meters: int | None) -> str:
    if meters is None:
        return "нет расстояния"
    if meters < 1000:
        return f"~{meters} м"
    km = meters / 1000
    if km < 10:
        return f"~{km:.1f} км"
    return f"~{round(km)} км"


def _traffic_label(route: RouteSummary) -> str:
    if route.duration_s is None or route.traffic_duration_s is None:
        if route.traffic_type == "realtime":
            return "пробки учтены"
        if route.traffic_type == "forecast":
            return "учтён прогноз трафика"
        return "пробки: нет отдельной оценки"
    ratio = route.traffic_duration_s / max(route.duration_s, 1)
    if ratio < 1.15:
        return "пробки лёгкие"
    if ratio < 1.45:
        return "пробки средние"
    return "пробки плотные"


class YandexMapsService:
    def __init__(self, timeout_seconds: float = 10.0) -> None:
        self.timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self.geocoder_url = "https://geocode-maps.yandex.ru/1.x/"
        self.router_url = "https://api.routing.yandex.net/v2/route"

    async def context_for(self, text: str) -> str:
        if not wants_maps_info(text):
            return ""

        settings = get_settings()
        if not settings.yandex_geocoder_api_key:
            return (
                "Yandex Maps context: запрос похож на карты/маршрут, "
                "но YANDEX_GEOCODER_API_KEY не задан."
            )

        route_request = parse_route_request(text)
        if route_request is None:
            if _is_traffic_question(text):
                return (
                    "Yandex Maps context: пользователь спрашивает про пробки, "
                    "но город или маршрут не указан. Нужно коротко спросить, где смотреть."
                )
            point = await self.geocode(text)
            if not point:
                return "Yandex Maps context: место не найдено."
            return (
                "Yandex Maps context:\n"
                f"Place: {point.title}\n"
                f"Address: {point.address}\n"
                f"Coordinates: {point.lat:.6f}, {point.lon:.6f}"
            )

        if not route_request.origin:
            return (
                "Yandex Maps context: пользователь спрашивает про маршрут, "
                "но стартовая точка не указана и YANDEX_MAPS_DEFAULT_ORIGIN пустой. "
                "Нужно коротко спросить, откуда строить маршрут."
            )

        origin = await self.geocode(route_request.origin)
        destination = await self.geocode(route_request.destination)
        if not origin or not destination:
            missing = []
            if not origin:
                missing.append("start")
            if not destination:
                missing.append("destination")
            return f"Yandex Maps context: не удалось найти {'/'.join(missing)}."

        summaries: list[RouteSummary] = []
        for mode in route_request.modes:
            route = await self.route(origin, destination, mode=mode)
            if route:
                summaries.append(route)

        straight = _haversine_m(origin.lat, origin.lon, destination.lat, destination.lon)
        lines = [
            "Yandex Maps context:",
            f"From: {origin.title} / {origin.address}",
            f"To: {destination.title} / {destination.address}",
            f"Straight-line distance: {_format_distance(round(straight))}",
        ]
        for route in summaries:
            label = "car" if route.mode == "driving" else "walk"
            duration = route.traffic_duration_s if route.mode == "driving" and route.traffic_duration_s else route.duration_s
            extra = f", {_traffic_label(route)}" if route.mode == "driving" else ""
            lines.append(f"{label}: {_format_duration(duration)}, {_format_distance(route.distance_m)}{extra}")
        if not summaries:
            lines.append("Route details unavailable; use straight-line distance only.")
        return "\n".join(lines)

    async def geocode(self, query: str) -> GeoPoint | None:
        settings = get_settings()
        params = {
            "apikey": settings.yandex_geocoder_api_key,
            "geocode": query,
            "format": "json",
            "lang": "ru_RU",
            "results": "1",
        }
        try:
            async with aiohttp.ClientSession(timeout=self.timeout) as session:
                async with session.get(self.geocoder_url, params=params) as response:
                    if response.status != 200:
                        logger.warning("yandex geocode failed", status=response.status, query=query)
                        return None
                    data = await response.json(content_type=None)
        except Exception as error:
            logger.warning("yandex geocode unavailable", query=query, error=str(error))
            return None

        try:
            item = data["response"]["GeoObjectCollection"]["featureMember"][0]["GeoObject"]
            lon_s, lat_s = item["Point"]["pos"].split()
            meta = item.get("metaDataProperty", {}).get("GeocoderMetaData", {})
            return GeoPoint(
                query=query,
                title=(item.get("name") or query).strip(),
                address=(meta.get("text") or item.get("description") or "").strip(),
                lon=float(lon_s),
                lat=float(lat_s),
            )
        except (KeyError, IndexError, TypeError, ValueError) as error:
            logger.warning("yandex geocode parse failed", query=query, error=str(error))
            return None

    async def route(self, origin: GeoPoint, destination: GeoPoint, mode: str) -> RouteSummary | None:
        settings = get_settings()
        if not settings.yandex_routing_api_key:
            logger.warning("yandex route skipped", reason="YANDEX_ROUTING_API_KEY is empty", mode=mode)
            return None
        params = {
            "apikey": settings.yandex_routing_api_key,
            "waypoints": f"{origin.lat},{origin.lon}|{destination.lat},{destination.lon}",
            "mode": mode,
            "traffic": "disabled",
        }
        try:
            async with aiohttp.ClientSession(timeout=self.timeout) as session:
                async with session.get(self.router_url, params=params) as response:
                    if response.status != 200:
                        body = await response.text()
                        logger.warning("yandex route failed", status=response.status, mode=mode, preview=body[:240])
                        return None
                    data = await response.json(content_type=None)
        except Exception as error:
            logger.warning("yandex route unavailable", mode=mode, error=str(error))
            return None

        return self._parse_route_summary(data, mode)

    def _parse_route_summary(self, data: dict[str, Any], mode: str) -> RouteSummary | None:
        route = _first_route(data)
        if not isinstance(route, dict):
            logger.warning("yandex route parse failed", reason="no route object")
            return None

        distance = _extract_metric(route, ("distance", "distance_meters", "distanceMeters"))
        duration = _extract_metric(route, ("duration", "duration_seconds", "durationSeconds"))
        traffic = _extract_metric(route, ("duration_in_traffic", "durationInTraffic", "durationWithTraffic"))
        if distance is None:
            distance = _sum_route_steps(route, "length")
        if duration is None:
            duration = _sum_route_steps(route, "duration")
        if distance is None and duration is None:
            logger.warning("yandex route parse failed", reason="metrics missing", keys=list(route.keys())[:12])
            return None
        return RouteSummary(
            mode=mode,
            distance_m=distance,
            duration_s=duration,
            traffic_duration_s=traffic,
            traffic_type=str(data.get("traffic_type") or ""),
        )


def _first_route(data: dict[str, Any]) -> dict[str, Any] | None:
    candidates: list[Any] = [
        data.get("route"),
        data.get("routes"),
        data.get("result", {}).get("route") if isinstance(data.get("result"), dict) else None,
        data.get("result", {}).get("routes") if isinstance(data.get("result"), dict) else None,
    ]
    for candidate in candidates:
        if isinstance(candidate, list) and candidate:
            return candidate[0] if isinstance(candidate[0], dict) else None
        if isinstance(candidate, dict):
            return candidate
    return None


def _is_traffic_question(text: str) -> bool:
    low = (text or "").lower()
    return any(word in low for word in ("пробк", "traffic", "затор"))


def _extract_metric(obj: dict[str, Any], keys: tuple[str, ...]) -> int | None:
    for key in keys:
        value = obj.get(key)
        metric = _metric_to_int(value)
        if metric is not None:
            return metric
    return None


def _metric_to_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return round(value)
    if isinstance(value, str):
        match = re.search(r"\d+(?:\.\d+)?", value)
        if match:
            return round(float(match.group(0)))
    if isinstance(value, dict):
        for key in ("value", "seconds", "meters", "duration", "distance"):
            metric = _metric_to_int(value.get(key))
            if metric is not None:
                return metric
    return None


def _sum_route_steps(route: dict[str, Any], metric_name: str) -> int | None:
    legs = route.get("legs") or []
    if not isinstance(legs, list):
        return None
    values: list[int] = []
    for leg in legs:
        if not isinstance(leg, dict):
            continue
        steps = leg.get("steps") or []
        if not isinstance(steps, list):
            continue
        for step in steps:
            if not isinstance(step, dict):
                continue
            value = _extract_metric(step, (metric_name,))
            if value is not None:
                values.append(value)
    if not values:
        return None
    return sum(values)


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6_371_000
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * radius * math.atan2(math.sqrt(a), math.sqrt(1 - a))
