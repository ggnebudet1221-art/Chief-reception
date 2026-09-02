from __future__ import annotations

from dataclasses import dataclass

from src.infrastructure.logging.logger import get_logger
from src.services.telegram_intelligence.config import TelegramIntelligenceConfig, default_telegram_intelligence_config
from src.services.telegram_intelligence.sources import NotConfiguredTelegramReader, TelegramChannelReader
from src.services.telegram_intelligence.types import MessageCandidate, MessageRating

logger = get_logger(__name__)


@dataclass(frozen=True)
class TelegramIntelligenceSnapshot:
    candidates: list[MessageCandidate]
    ratings: list[MessageRating]

    @property
    def useful_messages(self) -> list[MessageRating]:
        return [rating for rating in self.ratings if rating.usefulness_score >= 0.6]


class TelegramIntelligencePipeline:
    def __init__(
        self,
        config: TelegramIntelligenceConfig | None = None,
        reader: TelegramChannelReader | None = None,
    ) -> None:
        self._config = config or default_telegram_intelligence_config()
        self._reader = reader or NotConfiguredTelegramReader()

    async def collect(self) -> TelegramIntelligenceSnapshot:
        candidates: list[MessageCandidate] = []
        for channel in self._config.channels:
            if not channel.enabled:
                continue
            posts = await self._reader.fetch_recent_posts(channel, self._config.source_settings)
            candidates.extend(posts)

        ratings = [self._rate(candidate) for candidate in candidates]
        filtered = [
            rating
            for rating in ratings
            if rating.usefulness_score >= self._config.importance_filter.min_score
        ]
        logger.info(
            "Telegram Intelligence snapshot collected",
            channels=len(self._config.channels),
            candidates=len(candidates),
            useful=len(filtered),
        )
        return TelegramIntelligenceSnapshot(candidates=candidates, ratings=filtered)

    def _rate(self, candidate: MessageCandidate) -> MessageRating:
        text = (candidate.text or "").lower()
        preferred = set(self._config.importance_filter.preferred_categories)
        category_hit = bool(set(candidate.categories) & preferred) if preferred else True
        action_words = ("запуск", "mvp", "продажи", "кейс", "инструмент", "автоматиза", "agent", "ai")
        actionable = any(word in text for word in action_words)
        usefulness = 0.4
        if category_hit:
            usefulness += 0.25
        if actionable:
            usefulness += 0.25
        if len(candidate.text) > 120:
            usefulness += 0.1
        usefulness = min(usefulness, 1.0)
        return MessageRating(
            candidate=candidate,
            usefulness_score=usefulness,
            importance_score=usefulness,
            actionability_score=0.8 if actionable else 0.3,
            reason="Initial heuristic placeholder until LLM/rules rating is connected.",
            suggested_use="Use in Chief brief if it relates to AI agents, business automation, or monetization.",
        )
