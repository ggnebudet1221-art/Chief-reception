from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class InterestCategory(StrEnum):
    AI = "AI"
    AI_AGENTS = "AI-агенты"
    AUTOMATION = "автоматизация"
    BUSINESS = "бизнес"
    STARTUPS = "стартапы"
    SALES = "продажи"
    MARKETING = "маркетинг"
    PRODUCTIVITY = "продуктивность"
    TELEGRAM = "Telegram"
    TECHNOLOGY = "технологии"


@dataclass(frozen=True)
class ChannelSource:
    handle: str
    title: str = ""
    categories: tuple[InterestCategory, ...] = ()
    enabled: bool = True
    priority: int = 3
    notes: str = ""


@dataclass(frozen=True)
class SourceSettings:
    max_posts_per_channel: int = 20
    lookback_hours: int = 24
    language_hint: str = "ru"
    include_for_morning_brief: bool = True
    include_for_evening_reflection: bool = True


@dataclass(frozen=True)
class ImportanceFilter:
    min_score: float = 0.6
    require_actionability: bool = True
    reject_noise: bool = True
    preferred_categories: tuple[InterestCategory, ...] = ()


@dataclass(frozen=True)
class MessageCandidate:
    source_handle: str
    message_id: str
    text: str
    published_at: datetime | None = None
    url: str = ""
    categories: tuple[InterestCategory, ...] = ()
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class MessageRating:
    candidate: MessageCandidate
    usefulness_score: float
    importance_score: float
    actionability_score: float
    reason: str
    suggested_use: str = ""
