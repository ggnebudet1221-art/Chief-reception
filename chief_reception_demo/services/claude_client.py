from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

OFFICIAL_ANTHROPIC_BASE_URL = "https://api.anthropic.com"


@dataclass(frozen=True)
class AnthropicReceptionistResponse:
    reply: str
    extraction: dict[str, str]
    booking_intent: bool


class AnthropicReceptionistClient:
    def __init__(self, *, api_key: str, base_url: str, model: str, max_tokens: int) -> None:
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.max_tokens = max_tokens
        self._client = None

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    @property
    def provider(self) -> str:
        normalized = self.base_url.rstrip("/")
        if not normalized or normalized == OFFICIAL_ANTHROPIC_BASE_URL:
            return "official_anthropic"
        return "anthropic_compatible"

    async def complete(self, *, system_prompt: str, user_prompt: str) -> AnthropicReceptionistResponse | None:
        if not self.enabled:
            logger.info("[AI] provider=%s model=%s used_ai=False", self.provider, self.model)
            logger.info("[AI] ANTHROPIC_API_KEY is not configured. Using fallback receptionist mode.")
            return None

        try:
            client = self._get_client()
            logger.info("[AI] provider=%s model=%s sending request", self.provider, self.model)
            message = await client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                temperature=0.3,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
            content = _message_text(message)
            logger.info("[AI RAW RESPONSE]\n%s", content)
            payload = _extract_json_object(content)
            if payload is None:
                logger.warning("[AI] No JSON object found in successful model response. Using raw text as AI reply.")
                logger.info("[AI] provider=%s model=%s used_ai=True", self.provider, self.model)
                return AnthropicReceptionistResponse(
                    reply=content,
                    extraction=_string_fields({}),
                    booking_intent=False,
                )

            extraction = payload.get("extraction") if isinstance(payload.get("extraction"), dict) else payload
            reply = str(payload.get("reply") or "").strip()
            if not reply:
                logger.warning("[AI] JSON response did not contain reply. Using raw text as AI reply.")
                reply = content
            booking_intent = _bool_field(payload.get("booking_intent"))
            logger.info("[AI] Anthropic extraction=%s booking_intent=%s", extraction, booking_intent)
            logger.info("[AI] provider=%s model=%s used_ai=True", self.provider, self.model)
            return AnthropicReceptionistResponse(
                reply=reply,
                extraction=_string_fields(extraction),
                booking_intent=booking_intent,
            )
        except Exception:
            logger.info("[AI] provider=%s model=%s used_ai=False", self.provider, self.model)
            logger.exception("[AI] Anthropic-compatible request failed. Using fallback receptionist mode.")
            return None

    def _get_client(self):
        if self._client is not None:
            return self._client

        from anthropic import AsyncAnthropic

        kwargs = {"api_key": self.api_key}
        if self.provider != "official_anthropic":
            kwargs["base_url"] = self.base_url
        self._client = AsyncAnthropic(**kwargs)
        return self._client


ClaudeReceptionistResponse = AnthropicReceptionistResponse
ClaudeReceptionistClient = AnthropicReceptionistClient


def _message_text(message: Any) -> str:
    direct_text = getattr(message, "text", None)
    if direct_text:
        return str(direct_text).strip()

    parts: list[str] = []
    for block in getattr(message, "content", []):
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    return "\n".join(parts).strip()


def _extract_json_object(text: str) -> dict[str, Any] | None:
    stripped = text.strip()
    if stripped.startswith("```"):
        fenced_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", stripped, re.DOTALL | re.IGNORECASE)
        if fenced_match:
            parsed = _try_parse_json(fenced_match.group(1))
            if parsed is not None:
                return parsed

    parsed = _try_parse_json(stripped)
    if parsed is not None:
        return parsed

    for match in re.finditer(r"\{.*?\}", stripped, re.DOTALL):
        parsed = _try_parse_json(match.group(0))
        if parsed is not None:
            return parsed

    start = stripped.find("{")
    end = stripped.rfind("}")
    if start != -1 and end != -1 and end > start:
        return _try_parse_json(stripped[start : end + 1])
    return None


def _try_parse_json(text: str) -> dict[str, Any] | None:
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _string_fields(value: Any) -> dict[str, str]:
    result: dict[str, str] = {}
    if not isinstance(value, dict):
        return result
    for key in ("service", "date", "time", "client_name", "phone"):
        raw = value.get(key, "")
        result[key] = str(raw).strip() if raw is not None else ""
    return result


def _bool_field(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().casefold() in {"true", "yes", "1", "да"}
    return bool(value)
