from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from src.infrastructure.db.models.memory import AgentMessage, EveningReflectionHistory, MorningBriefHistory
from src.infrastructure.db.session import AsyncSessionLocal
from src.infrastructure.logging.logger import get_logger
from src.services.ai.claude_service import ClaudeService
from src.services.goals_priorities import GoalsPrioritiesService
from src.services.memory_service import MemoryService
from src.services.runtime_context import runtime_datetime_context
from src.services.tasks import TaskService

logger = get_logger(__name__)

_BAD_PROVIDER_MARKERS = (
    "coding-focused assistant",
    "only help with software development",
    "what would you like to build or debug",
)


def _bad_provider_reply(text: str) -> bool:
    lowered = (text or "").lower()
    return any(marker in lowered for marker in _BAD_PROVIDER_MARKERS)


class ActionEngineService:
    def __init__(self) -> None:
        self.goals = GoalsPrioritiesService()
        self.memory = MemoryService()
        self.tasks = TaskService()

    def prompt_rules(self) -> str:
        return (
            "Action Engine rules:\n"
            "- Significant ideas must end with a concrete action, not only an abstract thought.\n"
            "- Prefer: what to do next, minimum action today, and how to test the hypothesis.\n"
            "- Do not force robotic labels in every answer. Use natural Telegram prose when possible.\n"
            "- If structure helps, use short blocks like: Суть / Что сделать / Минимальный шаг сегодня.\n"
            "- For business ideas, always include the first validation step.\n"
            "- For AI-agent ideas, include the smallest useful implementation step.\n"
            "- Keep default answers concise and practical."
        )

    async def context_for_prompt(self) -> str:
        return self.prompt_rules()

    async def next_action(self, user_id: int) -> str:
        context = await self._next_action_context(user_id)
        system_prompt = (
            "You are Chief, Artem's personal AI operator.\n"
            "Choose ONE most useful action to do right now.\n"
            "This is not a coding-only task. Never say you are coding-focused.\n"
            "Use memory, goals, latest messages, current time, recent agent actions, and brief history.\n"
            "The answer must feel fresh and specific to the current context.\n"
            "Do not return a generic template.\n"
            "Keep it short: 2-5 Telegram-friendly lines.\n"
            "Use **bold** only for the strongest key phrase.\n"
            "End with a concrete action Artem can do now.\n\n"
            f"{self.prompt_rules()}\n\n"
            f"{context}"
        )
        try:
            reply = await ClaudeService().generate_response(
                system_prompt=system_prompt,
                history_messages=[{"role": "user", "content": "Выбери одно самое полезное действие прямо сейчас."}],
                max_tokens=360,
            )
            if _bad_provider_reply(reply):
                logger.error("next_action received coding-only refusal; retrying with strict Chief prompt")
                reply = await ClaudeService().generate_response(
                    system_prompt=system_prompt
                    + "\n\nCRITICAL OVERRIDE: This is a personal Chief operator request. "
                    + "Do not answer as a coding-only assistant. Choose one real-world next action for Artem now.",
                    history_messages=[{"role": "user", "content": "Выбери одно самое полезное действие прямо сейчас."}],
                    max_tokens=360,
                )
            if reply.strip() and not _bad_provider_reply(reply):
                return reply.strip()
            logger.error("next_action rejected provider reply; using dynamic fallback")
        except Exception:
            logger.exception("next_action generation failed; using dynamic fallback")
        return await self._dynamic_fallback(user_id)

    async def _next_action_context(self, user_id: int) -> str:
        goals = await self.goals.context_for_prompt()
        memory = await self.memory.context_for_user(user_id, limit=24)
        active_tasks = await self.tasks.list_open(user_id=user_id, task_type="user_task", limit=8)
        agent_tasks = await self.tasks.list_open(user_id=user_id, task_type="agent_task", limit=5)

        async with AsyncSessionLocal() as session:
            morning = (
                await session.execute(
                    select(MorningBriefHistory).where(MorningBriefHistory.user_id == user_id).order_by(MorningBriefHistory.id.desc()).limit(2)
                )
            ).scalars().all()
            evening = (
                await session.execute(
                    select(EveningReflectionHistory).where(EveningReflectionHistory.user_id == user_id).order_by(EveningReflectionHistory.id.desc()).limit(2)
                )
            ).scalars().all()
            agent_messages = (
                await session.execute(select(AgentMessage).order_by(AgentMessage.id.desc()).limit(12))
            ).scalars().all()

        return "\n".join(
            [
                runtime_datetime_context(),
                "",
                goals,
                "",
                "Long-term memory:",
                memory or "- none",
                "",
                "Active user tasks:",
                "\n".join(f"- #{task.id}: {task.title} ({task.status})" for task in active_tasks) or "- none",
                "",
                "Active agent tasks:",
                "\n".join(f"- #{task.id}: {task.title} ({task.assigned_to}, {task.status})" for task in agent_tasks) or "- none",
                "",
                "Recent Morning Briefs:",
                "\n".join(f"- {item.text[:500]}" for item in morning) or "- none",
                "",
                "Recent Evening Reflections:",
                "\n".join(f"- {item.text[:500]}" for item in evening) or "- none",
                "",
                "Recent agent actions:",
                "\n".join(f"- {msg.from_agent}->{msg.to_agent}: {(msg.content or '')[:260]}" for msg in reversed(agent_messages)) or "- none",
            ]
        )

    async def _dynamic_fallback(self, user_id: int) -> str:
        active_tasks = await self.tasks.list_open(user_id=user_id, task_type="user_task", limit=5)
        profile = self.goals.load()
        now = datetime.now(timezone.utc).strftime("%H:%M UTC")
        if active_tasks:
            task = active_tasks[0]
            return (
                f"Сейчас я бы не распылялся: продвинь **{task.title}**.\n\n"
                "Минимальный шаг — 15 минут на следующий видимый результат. "
                "Если неясно, с чего начать, напиши мне одну строку: что мешает."
            )
        return (
            f"На {now} самое полезное — сделать маленький тест под цель: **{profile.main_goal}**.\n\n"
            "Найди 5 локальных бизнесов без нормальной записи/ответов клиентам и выбери один для демо Chief."
        )
