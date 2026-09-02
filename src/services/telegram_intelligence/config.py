from __future__ import annotations

from dataclasses import dataclass

from src.services.telegram_intelligence.types import (
    ChannelSource,
    ImportanceFilter,
    InterestCategory,
    SourceSettings,
)


DEFAULT_INTEREST_CATEGORIES: tuple[InterestCategory, ...] = (
    InterestCategory.AI,
    InterestCategory.AI_AGENTS,
    InterestCategory.AUTOMATION,
    InterestCategory.BUSINESS,
    InterestCategory.STARTUPS,
    InterestCategory.SALES,
    InterestCategory.MARKETING,
    InterestCategory.PRODUCTIVITY,
    InterestCategory.TELEGRAM,
    InterestCategory.TECHNOLOGY,
)


@dataclass(frozen=True)
class TelegramIntelligenceConfig:
    channels: tuple[ChannelSource, ...]
    categories: tuple[InterestCategory, ...]
    source_settings: SourceSettings
    importance_filter: ImportanceFilter


def default_telegram_intelligence_config() -> TelegramIntelligenceConfig:
    return TelegramIntelligenceConfig(
        channels=(
            # Fill these later with real channel handles, for example:
            # ChannelSource(handle="@some_ai_channel", title="AI Channel", categories=(InterestCategory.AI,)),
        ),
        categories=DEFAULT_INTEREST_CATEGORIES,
        source_settings=SourceSettings(),
        importance_filter=ImportanceFilter(
            min_score=0.6,
            require_actionability=True,
            reject_noise=True,
            preferred_categories=DEFAULT_INTEREST_CATEGORIES,
        ),
    )
