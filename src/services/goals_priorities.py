from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.infrastructure.logging.logger import get_logger

logger = get_logger(__name__)

DEFAULT_PROFILE_PATH = Path("data/user_goals_priorities.json")

DEFAULT_GOALS_PROFILE: dict[str, Any] = {
    "main_goal": "монетизировать AI-агентов и автоматизацию бизнеса",
    "current_focus": "развитие Chief / AI Manager",
    "secondary_focus": [
        "поиск бизнес-кейсов для автоматизации",
        "Telegram AI workspace",
        "полезные AI-инструменты",
        "обучение предпринимательству",
        "спорт и дисциплина",
    ],
    "interests": [
        "AI",
        "AI agents",
        "автоматизация",
        "бизнес",
        "Telegram",
        "стартапы",
        "SMM",
        "продажи",
        "малый бизнес",
        "продуктивность",
    ],
    "ignore_topics": [
        "политика",
        "криптошум",
        "абстрактная мотивация",
        "новости без практического применения",
        "длинная теория без действия",
    ],
    "response_preferences": [
        "коротко по умолчанию",
        "практично",
        "без воды",
        "с конкретным следующим шагом",
        "важные мысли выделять жирным",
        "если тема важная — можно дать средний по длине ответ",
        "не писать огромные простыни без запроса",
    ],
}


@dataclass(frozen=True)
class GoalsPrioritiesProfile:
    main_goal: str
    current_focus: str
    secondary_focus: list[str]
    interests: list[str]
    ignore_topics: list[str]
    response_preferences: list[str]


def _clean_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [" ".join(str(item).split()).strip() for item in value if " ".join(str(item).split()).strip()]


class GoalsPrioritiesService:
    def __init__(self, path: Path | str = DEFAULT_PROFILE_PATH) -> None:
        self.path = Path(path)

    def ensure_file(self) -> None:
        if self.path.exists():
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(DEFAULT_GOALS_PROFILE, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        logger.info("default goals/priorities profile created", path=str(self.path))

    def load(self) -> GoalsPrioritiesProfile:
        self.ensure_file()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            logger.exception("goals/priorities profile is invalid; using defaults", path=str(self.path))
            raw = DEFAULT_GOALS_PROFILE
        merged = {**DEFAULT_GOALS_PROFILE, **(raw if isinstance(raw, dict) else {})}
        return GoalsPrioritiesProfile(
            main_goal=" ".join(str(merged.get("main_goal", "")).split()).strip() or DEFAULT_GOALS_PROFILE["main_goal"],
            current_focus=" ".join(str(merged.get("current_focus", "")).split()).strip()
            or DEFAULT_GOALS_PROFILE["current_focus"],
            secondary_focus=_clean_list(merged.get("secondary_focus")) or list(DEFAULT_GOALS_PROFILE["secondary_focus"]),
            interests=_clean_list(merged.get("interests")) or list(DEFAULT_GOALS_PROFILE["interests"]),
            ignore_topics=_clean_list(merged.get("ignore_topics")) or list(DEFAULT_GOALS_PROFILE["ignore_topics"]),
            response_preferences=_clean_list(merged.get("response_preferences"))
            or list(DEFAULT_GOALS_PROFILE["response_preferences"]),
        )

    async def context_for_prompt(self) -> str:
        profile = self.load()
        return (
            "Goals & priorities:\n"
            f"- Main goal: {profile.main_goal}\n"
            f"- Current focus: {profile.current_focus}\n"
            "- Secondary focus:\n"
            + "\n".join(f"  - {item}" for item in profile.secondary_focus)
            + "\n- Interests:\n"
            + "\n".join(f"  - {item}" for item in profile.interests)
            + "\n- Ignore topics:\n"
            + "\n".join(f"  - {item}" for item in profile.ignore_topics)
            + "\n- Response preferences:\n"
            + "\n".join(f"  - {item}" for item in profile.response_preferences)
        )

    async def telegram_profile_text(self) -> str:
        profile = self.load()
        return (
            "<b>Goals & Priorities</b>\n\n"
            f"<b>Main goal</b>\n{profile.main_goal}\n\n"
            f"<b>Current focus</b>\n{profile.current_focus}\n\n"
            "<b>Secondary focus</b>\n"
            + "\n".join(f"• {item}" for item in profile.secondary_focus)
            + "\n\n<b>Interests</b>\n"
            + "\n".join(f"• {item}" for item in profile.interests)
            + "\n\n<b>Ignore topics</b>\n"
            + "\n".join(f"• {item}" for item in profile.ignore_topics)
            + "\n\n<b>Response preferences</b>\n"
            + "\n".join(f"• {item}" for item in profile.response_preferences)
        )

