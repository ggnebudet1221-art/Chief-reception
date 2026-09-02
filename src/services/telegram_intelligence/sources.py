from __future__ import annotations

from typing import Protocol

from src.infrastructure.logging.logger import get_logger
from src.services.telegram_intelligence.types import ChannelSource, MessageCandidate, SourceSettings

logger = get_logger(__name__)


class TelegramChannelReader(Protocol):
    async def fetch_recent_posts(
        self,
        channel: ChannelSource,
        settings: SourceSettings,
    ) -> list[MessageCandidate]:
        ...


class NotConfiguredTelegramReader:
    async def fetch_recent_posts(
        self,
        channel: ChannelSource,
        settings: SourceSettings,
    ) -> list[MessageCandidate]:
        logger.info(
            "Telegram Intelligence source skipped; reader is not configured yet",
            channel=channel.handle,
            max_posts=settings.max_posts_per_channel,
            lookback_hours=settings.lookback_hours,
        )
        return []
