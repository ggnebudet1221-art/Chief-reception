from __future__ import annotations

import logging

from chief_reception_demo.services.claude_client import OFFICIAL_ANTHROPIC_BASE_URL

logger = logging.getLogger(__name__)


class ClaudeDialogClient:
    def __init__(self, *, api_key: str, base_url: str, model: str, max_tokens: int) -> None:
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.max_tokens = max_tokens
        self._client = None

    async def complete(self, *, system_prompt: str, messages: list[dict[str, str]]) -> str:
        client = self._get_client()
        logger.info("[AI] provider=%s model=%s userbot_request=True", self.provider, self.model)
        response = await client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            temperature=0.35,
            system=system_prompt,
            messages=messages,
        )
        text = _message_text(response)
        logger.info("[AI RAW RESPONSE]\n%s", text)
        return text

    @property
    def provider(self) -> str:
        if self.base_url.rstrip("/") == OFFICIAL_ANTHROPIC_BASE_URL:
            return "official_anthropic"
        return "anthropic_compatible"

    def _get_client(self):
        if self._client is not None:
            return self._client

        from anthropic import AsyncAnthropic

        kwargs = {"api_key": self.api_key}
        if self.provider != "official_anthropic":
            kwargs["base_url"] = self.base_url
        self._client = AsyncAnthropic(**kwargs)
        return self._client


def _message_text(message) -> str:
    parts: list[str] = []
    for block in getattr(message, "content", []):
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    return "\n".join(parts).strip()
