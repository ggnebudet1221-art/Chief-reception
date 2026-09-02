from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from chief_reception_demo.database.repositories import ServiceRepository
from chief_reception_demo.services.availability_service import AvailabilityService
from chief_reception_demo.services.claude_client import AnthropicReceptionistClient
from chief_reception_demo.services.receptionist_fallback import (
    extract_name,
    extract_phone,
    is_probable_name,
    is_valid_client_name,
    normalize_phone,
    service_aliases,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExtractionResult:
    reply: str | None
    fields: dict[str, str]
    used_ai: bool


class ExtractionService:
    def __init__(
        self,
        *,
        claude: AnthropicReceptionistClient,
        services: ServiceRepository,
        availability: AvailabilityService,
        salon_name: str,
    ) -> None:
        self.claude = claude
        self.services = services
        self.availability = availability
        self.salon_name = salon_name

    async def extract(
        self,
        *,
        message_text: str,
        conversation_state: dict[str, Any],
        telegram_first_name: str | None,
        telegram_username: str | None,
    ) -> ExtractionResult:
        system_prompt = self._system_prompt()
        user_prompt = self._user_prompt(
            message_text=message_text,
            conversation_state=conversation_state,
            telegram_first_name=telegram_first_name,
            telegram_username=telegram_username,
        )
        response = await self.claude.complete(system_prompt=system_prompt, user_prompt=user_prompt)
        if response:
            fields = self.normalize_fields(
                response.extraction,
                fallback_text=message_text,
                conversation_state=conversation_state,
                telegram_first_name=telegram_first_name,
            )
            fields["booking_intent"] = "true" if response.booking_intent else ""
            logger.info("[AI] used_ai=True")
            return ExtractionResult(reply=response.reply, fields=fields, used_ai=True)

        return ExtractionResult(
            reply=None,
            fields={
                **self.normalize_fields(
                    {},
                    fallback_text=message_text,
                    conversation_state=conversation_state,
                    telegram_first_name=telegram_first_name,
                ),
                "booking_intent": "",
            },
            used_ai=False,
        )

    def normalize_fields(
        self,
        fields: dict[str, str],
        *,
        fallback_text: str,
        conversation_state: dict[str, Any],
        telegram_first_name: str | None,
    ) -> dict[str, str]:
        normalized = {key: (fields.get(key) or "").strip() for key in ("service", "date", "time", "client_name", "phone")}

        service = self._normalize_service(normalized["service"]) or self._normalize_service(fallback_text)
        normalized["service"] = service or ""

        allow_bare_day = conversation_state.get("awaiting") != "time"
        parsed_date = self.availability.parse_date(normalized["date"]) if normalized["date"] else None
        normalized["date"] = parsed_date or self.availability.parse_date(fallback_text, allow_bare_day=allow_bare_day) or ""

        parsed_time = self.availability.parse_time(normalized["time"]) if normalized["time"] else None
        normalized["time"] = parsed_time or self.availability.parse_time(fallback_text) or ""

        explicit_name = extract_name(fallback_text)
        if explicit_name:
            normalized["client_name"] = explicit_name
        elif (
            normalized["client_name"]
            and telegram_first_name
            and normalized["client_name"].casefold() == telegram_first_name.casefold()
            and conversation_state.get("awaiting") != "client_name"
        ):
            normalized["client_name"] = ""
        if (
            not normalized["client_name"]
            and conversation_state.get("awaiting") == "client_name"
            and is_probable_name(fallback_text)
        ):
            normalized["client_name"] = fallback_text.strip()
        if normalized["client_name"] and not is_valid_client_name(normalized["client_name"]):
            normalized["invalid_name"] = "true"
            normalized["client_name"] = ""
        raw_phone = normalized["phone"] or ""
        if raw_phone:
            normalized_phone = normalize_phone(raw_phone)
            if normalized_phone:
                normalized["phone"] = normalized_phone
            else:
                normalized["phone"] = ""
                normalized["invalid_phone"] = "true"
        else:
            normalized["phone"] = extract_phone(fallback_text) or ""
            if not normalized["phone"] and conversation_state.get("awaiting") == "phone" and re.search(r"\d", fallback_text):
                normalized["invalid_phone"] = "true"
        normalized["period"] = self.availability.parse_period(fallback_text) or ""
        logger.info("[AI] Normalized extraction=%s", normalized)
        return normalized

    def _normalize_service(self, value: str) -> str | None:
        if not value:
            return None
        normalized = value.casefold()
        for service in self.services.list_all():
            if service.title.casefold() in normalized or service.id.casefold() in normalized:
                return service.title
            for alias in service_aliases().get(service.id, ()):
                if alias.casefold() in normalized:
                    return service.title
        return None

    def _system_prompt(self) -> str:
        return (
            "You are an AI receptionist for a beauty salon and massage studio. "
            "Speak Russian by default unless the client writes in another language. "
            "Be polite, professional, warm, and natural. No jokes, no slang, no robotic menu style. "
            "Never invent services, dates, times, prices, or availability. "
            "Use only the provided services and available slots. "
            "If information is missing, ask one natural follow-up question. "
            "You must answer ONLY with one valid JSON object. "
            "Do not include markdown, code fences, explanations, prefixes, or text outside JSON. "
            "The JSON object must have exactly these keys: "
            "reply, service, date, time, client_name, phone, booking_intent. "
            "Example: "
            "{\"reply\":\"...\",\"service\":\"\",\"date\":\"\",\"time\":\"\",\"client_name\":\"\",\"phone\":\"\",\"booking_intent\":true}. "
            "Use empty strings for unknown values. Use service titles exactly as provided. "
            "Use date as YYYY-MM-DD when possible and time as HH:MM. "
            "booking_intent must be true when the client wants to book, asks about booking, or describes a need that can lead to booking."
        )

    def _user_prompt(
        self,
        *,
        message_text: str,
        conversation_state: dict[str, Any],
        telegram_first_name: str | None,
        telegram_username: str | None,
    ) -> str:
        services = [
            {
                "title": service.title,
                "price_rub": service.price_rub,
                "duration_minutes": service.duration_minutes,
                "description": service.description,
            }
            for service in self.services.list_all()
        ]
        current = self.availability.current_context()
        return (
            f"Salon name: {self.salon_name}\n"
            f"Текущая дата: {current['date']}\n"
            f"Текущий день недели: {current['weekday']}\n"
            f"Часовой пояс: {current['timezone']}\n"
            f"Services: {services}\n"
            f"Available slots for any selected date: {self.availability.available_times()}\n"
            f"Conversation state collected so far: {conversation_state}\n"
            f"Conversation history so far: {conversation_state.get('history', [])}\n"
            f"Telegram first_name: {telegram_first_name or ''}\n"
            f"Telegram username exists: {bool(telegram_username)}\n"
            f"Client message: {message_text}\n\n"
            "If the client says evening, offer only evening slots from the available slots. "
            "Use the existing conversation state and never ask again for data already present there. "
            "Telegram first_name is profile metadata, not confirmed client_name. "
            "Do not put Telegram first_name into client_name unless the client explicitly gave that name in the conversation. "
            "If selected date already exists, do not ask for date again. "
            "If selected time already exists, do not ask for time again. "
            "If phone already exists, do not ask for phone again. "
            "If the client describes a need such as back pain after training, infer the most suitable listed service, "
            "but do not claim medical advice. "
            "If enough booking data is present, reply that the booking is being arranged, not that it is saved yet."
        )
