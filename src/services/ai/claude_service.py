from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from anthropic import APIConnectionError, APITimeoutError, AsyncAnthropic, RateLimitError

from src.core.config import get_settings


class ClaudeServiceError(Exception):
    """Base exception for Claude service errors."""


class ClaudeConfigurationError(ClaudeServiceError):
    """Raised when Claude configuration is invalid."""


class ClaudeTemporaryError(ClaudeServiceError):
    """Raised for temporary Claude API failures."""


class ClaudeEmptyResponseError(ClaudeServiceError):
    """Raised when Claude returns no textual content."""


@dataclass(frozen=True)
class ClaudeRuntimeInfo:
    provider: str
    model: str
    base_url: str


class ClaudeService:
    _DEFAULT_MAX_TOKENS: Final[int] = 300

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        provider: str = "anthropic",
    ) -> None:
        settings = get_settings()

        resolved_api_key = api_key if api_key is not None else settings.anthropic_api_key
        resolved_base_url = base_url if base_url is not None else settings.anthropic_base_url
        resolved_model = model or settings.anthropic_model

        if not resolved_api_key.strip():
            raise ClaudeConfigurationError("ANTHROPIC_API_KEY is not configured.")

        client_kwargs: dict[str, str] = {"api_key": resolved_api_key}
        if resolved_base_url.strip():
            client_kwargs["base_url"] = resolved_base_url

        self._client = AsyncAnthropic(**client_kwargs)
        self._model = resolved_model
        self._runtime_info = ClaudeRuntimeInfo(
            provider=provider,
            model=resolved_model,
            base_url=resolved_base_url.strip() or "https://api.anthropic.com",
        )

    @property
    def runtime_info(self) -> ClaudeRuntimeInfo:
        return self._runtime_info

    async def generate_response(
        self,
        system_prompt: str,
        history_messages: list[dict[str, str]],
        max_tokens: int | None = None,
    ) -> str:
        try:
            async with self._client.messages.stream(
                model=self._model,
                max_tokens=max_tokens or self._DEFAULT_MAX_TOKENS,
                system=system_prompt,
                messages=history_messages,
            ) as stream:
                response_text = await stream.get_final_text()
        except AssertionError as exc:
            raise ClaudeEmptyResponseError("Claude API stream ended without a final message.") from exc
        except RuntimeError as exc:
            if "at least 1 text block" in str(exc):
                raise ClaudeEmptyResponseError("Claude API returned an empty response.") from exc
            raise
        except RateLimitError as exc:
            raise ClaudeTemporaryError("Claude API rate limit reached (429).") from exc
        except APITimeoutError as exc:
            raise ClaudeTemporaryError("Claude API request timed out.") from exc
        except APIConnectionError as exc:
            raise ClaudeTemporaryError("Claude API connection error.") from exc

        if not response_text or not response_text.strip():
            raise ClaudeEmptyResponseError("Claude API returned an empty response.")

        return response_text.strip()
