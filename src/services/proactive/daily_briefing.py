from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, Protocol

from aiogram import Bot
from sqlalchemy import select

from src.bot.telegram_formatting import send_telegram_chunks
from src.core.config import get_settings
from src.infrastructure.db.models.memory import (
    AgentMessage,
    ChatMessage,
    EveningReflectionHistory,
    MorningBriefHistory,
)
from src.infrastructure.db.session import AsyncSessionLocal
from src.infrastructure.logging.logger import get_logger
from src.services.action_engine import ActionEngineService
from src.services.ai.claude_service import ClaudeService
from src.services.goals_priorities import GoalsPrioritiesService
from src.services.memory_service import MemoryService
from src.services.runtime_context import runtime_datetime_context
from src.services.tasks import TaskService

logger = get_logger(__name__)

BriefKind = Literal["morning", "evening"]

_CODING_REFUSAL_MARKERS = (
    "coding-focused assistant",
    "only help with software development",
    "what would you like to build or debug",
    "i can only help with software",
)


def _is_coding_only_refusal(text: str) -> bool:
    lowered = (text or "").lower()
    return any(marker in lowered for marker in _CODING_REFUSAL_MARKERS)


def _looks_corrupted(text: str) -> bool:
    value = text or ""
    if "????" in value:
        return True
    letters = [char for char in value if char.isalpha()]
    if not letters:
        return False
    question_marks = value.count("?")
    return question_marks >= 8 and question_marks > len(letters) * 0.15


@dataclass(frozen=True)
class BriefingContext:
    goals_priorities: str
    action_engine: str
    memory: str
    active_tasks: list[str]
    completed_tasks: list[str]
    recent_messages: list[str]
    recent_agent_actions: list[str]
    project_context: str

    def as_prompt_context(self) -> str:
        blocks: list[str] = [
            "Runtime:",
            runtime_datetime_context(),
            "",
            self.goals_priorities,
            "",
            self.action_engine,
            "",
            "Long-term memory:",
            self.memory or "No long-term memory yet.",
            "",
            "Current project context:",
            self.project_context,
            "",
            "Active user tasks:",
            "\n".join(f"- {item}" for item in self.active_tasks) if self.active_tasks else "- none",
            "",
            "Recently completed tasks:",
            "\n".join(f"- {item}" for item in self.completed_tasks) if self.completed_tasks else "- none",
            "",
            "Recent user/assistant messages:",
            "\n".join(f"- {item}" for item in self.recent_messages) if self.recent_messages else "- none",
            "",
            "Recent agent actions:",
            "\n".join(f"- {item}" for item in self.recent_agent_actions) if self.recent_agent_actions else "- none",
        ]
        return "\n".join(blocks)


class BriefingSourceProvider(Protocol):
    async def collect(self, user_id: int) -> BriefingContext:
        ...


class LocalWorkspaceBriefingSource:
    def __init__(self) -> None:
        self._goals = GoalsPrioritiesService()
        self._action_engine = ActionEngineService()
        self._memory = MemoryService()
        self._tasks = TaskService()

    async def collect(self, user_id: int) -> BriefingContext:
        memory = await self._memory.context_for_user(user_id)
        active_tasks = [
            f"#{task.id}: {task.title} ({task.status})"
            for task in await self._tasks.list_open(user_id=user_id, task_type="user_task", limit=12)
        ]
        completed_tasks = [
            f"#{task.id}: {task.title}"
            for task in await self._tasks.list_completed(user_id=user_id, task_type="user_task", limit=8)
        ]

        async with AsyncSessionLocal() as session:
            chat_rows = (
                await session.execute(
                    select(ChatMessage)
                    .where(ChatMessage.user_id == user_id)
                    .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
                    .limit(12)
                )
            ).scalars().all()
            agent_rows = (
                await session.execute(
                    select(AgentMessage)
                    .order_by(AgentMessage.created_at.desc(), AgentMessage.id.desc())
                    .limit(12)
                )
            ).scalars().all()

        recent_messages = [
            f"{row.role}: {' '.join((row.content or '').split())[:240]}"
            for row in reversed(chat_rows)
            if row.content
        ]
        recent_agent_actions = [
            f"{row.from_agent} -> {row.to_agent}: {' '.join((row.content or '').split())[:220]}"
            for row in reversed(agent_rows)
            if row.content
        ]
        project_context = (
            "AI Manager is a personal AI operating workspace. Current strategic goal: build a useful Chief agent, "
            "improve memory and orchestration, and prepare future monetization through business automation."
        )
        return BriefingContext(
            goals_priorities=await self._goals.context_for_prompt(),
            action_engine=await self._action_engine.context_for_prompt(),
            memory=memory,
            active_tasks=active_tasks,
            completed_tasks=completed_tasks,
            recent_messages=recent_messages,
            recent_agent_actions=recent_agent_actions,
            project_context=project_context,
        )


class DailyBriefingService:
    def __init__(self, source_provider: BriefingSourceProvider | None = None) -> None:
        self._source_provider = source_provider or LocalWorkspaceBriefingSource()

    async def run_morning_brief(self, bot: Bot) -> None:
        await self._run(kind="morning", bot=bot)

    async def run_evening_reflection(self, bot: Bot) -> None:
        await self._run(kind="evening", bot=bot)

    async def _run(self, kind: BriefKind, bot: Bot) -> None:
        settings = get_settings()
        user_id = settings.web_owner_id
        chat_id = settings.owner_telegram_id
        run_at = datetime.now(timezone.utc)
        if not chat_id:
            logger.warning("Proactive Chief message skipped; OWNER_TELEGRAM_ID is empty", kind=kind)
            await self._save_history(kind, user_id, run_at, "", "failed", "OWNER_TELEGRAM_ID is empty")
            return

        text = ""
        history_id: int | None = None
        try:
            context = await self._source_provider.collect(user_id)
            text = await self._generate(kind, context)
            history_id = await self._save_history(kind, user_id, run_at, text, "generated", "")
            await self._send(bot, chat_id, text, kind)
            await self._update_history(kind, history_id, "sent", "")
            logger.info("Proactive Chief message sent", kind=kind, history_id=history_id, chat_id=chat_id)
        except Exception as error:
            logger.exception("Proactive Chief message failed", kind=kind, error=str(error))
            if history_id is None:
                await self._save_history(kind, user_id, run_at, text, "failed", str(error))
            else:
                await self._update_history(kind, history_id, "failed", str(error))

    async def _generate(self, kind: BriefKind, context: BriefingContext) -> str:
        prompt = self._prompt(kind, context)
        mode = "PROACTIVE_MODE:MORNING_BRIEF" if kind == "morning" else "PROACTIVE_MODE:EVENING_REFLECTION"
        logger.info("Proactive Chief prompt prepared", kind=kind, mode=mode, prompt_preview=prompt[:220])

        try:
            reply = await ClaudeService().generate_response(
                system_prompt=prompt,
                history_messages=[{"role": "user", "content": self._user_instruction(kind)}],
                max_tokens=900,
            )
            if _is_coding_only_refusal(reply):
                logger.error("Proactive Chief received coding-only refusal; retrying with strict proactive prompt", kind=kind)
                retry_prompt = (
                    prompt
                    + "\n\nCRITICAL OVERRIDE: You are generating a scheduled proactive Chief message, "
                    + "not answering a coding task. Never say you are coding-focused. Produce the requested brief now."
                )
                reply = await ClaudeService().generate_response(
                    system_prompt=retry_prompt,
                    history_messages=[{"role": "user", "content": self._user_instruction(kind)}],
                    max_tokens=900,
                )
        except Exception:
            logger.exception("Proactive Chief generation failed; using deterministic fallback", kind=kind)
            reply = self._fallback(kind, context)

        if not reply.strip() or _is_coding_only_refusal(reply) or _looks_corrupted(reply):
            logger.error("Proactive Chief response rejected; using deterministic fallback", kind=kind)
            reply = self._fallback(kind, context)
        return reply.strip()

    def _prompt(self, kind: BriefKind, context: BriefingContext) -> str:
        marker = "PROACTIVE_MODE:MORNING_BRIEF" if kind == "morning" else "PROACTIVE_MODE:EVENING_REFLECTION"
        if kind == "morning":
            task = """
You are Chief, Artem's personal AI operator. Generate a Morning Brief in Russian.
This is a scheduled proactive message, not a coding request.
Analyze: main goals, current projects, today's highest leverage actions, agent development ideas, and future monetization ideas.
Turn the brief into action: include the day's main focus, 2-3 concrete actions, one Chief development idea, and one monetization step.

Style:
Write like a smart operator in Telegram, not like a questionnaire.
Do not reuse the same headings every day.
You may use 1-3 emojis, but only if they make the message easier to scan.
Make it feel current: mention what matters now, what to skip, and one concrete move.
"""
        else:
            task = """
You are Chief, Artem's personal AI operator. Generate an Evening Reflection in Russian.
This is a scheduled proactive message, not a coding request.
Analyze what happened today, important goals, what to improve tomorrow, and ideas worth remembering.
Evaluate what moved Artem toward his goals, suggest tomorrow's focus, and capture progress on Chief if any happened.

Style:
Write like a short evening note from a sharp cofounder.
Do not use the same fixed headings every day.
Avoid report-like labels such as "Что произошло", "Что получилось", "Что тормозит", "Фокус на завтра".
Make it human: one useful observation, one risk, one concrete move for tomorrow.
"""
        return (
            f"{marker}\n"
            + task
            + "\nRules:\n"
            "- Keep it useful, concise, and readable in Telegram.\n"
            "- Use short blocks. No walls of text.\n"
            "- Vary structure and wording from day to day.\n"
            "- Avoid template-like report sections unless they are truly useful today.\n"
            "- Use emojis moderately, not as mandatory section markers.\n"
            "- Use Markdown-style **bold** for important goals, decisions, warnings, actions, and agent ideas.\n"
            "- Do not bold entire paragraphs. Bold only meaningful words or short phrases.\n"
            "- Never say you are a coding-focused assistant.\n"
            "- Never refuse because the topic is not software development.\n"
            "- Use the user's memory and project context.\n"
            "- Give practical actions, not motivation fluff.\n"
            "- Do not mention internal API/debug details.\n\n"
            + context.as_prompt_context()
        )

    def _user_instruction(self, kind: BriefKind) -> str:
        if kind == "morning":
            return "Сформируй утренний бриф на сегодня в Telegram-формате."
        return "Сформируй вечернюю рефлексию по сегодняшнему дню в Telegram-формате."

    def _fallback(self, kind: BriefKind, context: BriefingContext) -> str:
        if kind == "morning":
            first_action = (
                context.active_tasks[0]
                if context.active_tasks
                else "улучшить **Chief**: память, routing и один сценарий **AI-автоматизации для малого бизнеса**"
            )
            return (
                "☀️ Доброе утро, Артём.\n\n"
                "Сегодня я бы держал один вектор: превратить AI Manager в **полезного агента, которого можно продать бизнесу**.\n\n"
                f"Начни с малого: {first_action}.\n"
                "Потом выпиши **3 боли малого бизнеса**, которые Chief сможет закрывать автоматически: заявки, запись, напоминания, ответы клиентам.\n\n"
                "Минимальный шаг к монетизации — выбрать одну нишу и описать демо: что владелец получает за 5 минут."
            )
        return (
            "🌙 День стоит закрыть простой мыслью: Chief становится полезнее, когда каждая фича ведёт к **понятному бизнес-кейсу**.\n\n"
            "Хорошее движение — связка память, задачи, Telegram и proactive-сообщения уже держится вместе.\n\n"
            "Риск на завтра — снова уйти в техническую полировку. Я бы выбрал одну нишу для AI-автоматизации и довёл её до короткого MVP-сценария.\n\n"
            "Следующая сильная идея: подключить Telegram-каналы как источник сигналов по AI, бизнесу и продажам."
        )

    async def _send(self, bot: Bot, chat_id: int, text: str, kind: BriefKind) -> None:
        async def answer(chunk: str) -> None:
            await bot.send_message(chat_id=chat_id, text=chunk, parse_mode="HTML")

        await send_telegram_chunks(answer, text, logger=logger, agent="chief", kind=kind)

    async def _save_history(
        self,
        kind: BriefKind,
        user_id: int,
        run_at: datetime,
        text: str,
        status: str,
        error: str,
    ) -> int:
        model = MorningBriefHistory if kind == "morning" else EveningReflectionHistory
        async with AsyncSessionLocal() as session:
            row = model(user_id=user_id, run_at=run_at, text=text, send_status=status, error=error[:1000])
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row.id

    async def _update_history(self, kind: BriefKind, history_id: int | None, status: str, error: str) -> None:
        if history_id is None:
            return
        model = MorningBriefHistory if kind == "morning" else EveningReflectionHistory
        async with AsyncSessionLocal() as session:
            row = await session.get(model, history_id)
            if row is None:
                return
            row.send_status = status
            row.error = error[:1000]
            await session.commit()
