from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select

from src.core.config import get_settings
from src.infrastructure.db.models.memory import AgentMessage, ChatMessage, DayPlanItem, Reminder, Task, UserProfile
from src.infrastructure.db.session import AsyncSessionLocal
from src.infrastructure.logging.logger import get_logger
from src.services.action_engine import ActionEngineService
from src.services.agents.registry import AgentDefinition, AgentRegistry
from src.services.ai.claude_service import ClaudeService
from src.services.goals_priorities import GoalsPrioritiesService
from src.services.live_info import LiveInfoService
from src.services.maps import YandexMapsService
from src.services.memory_service import MemoryService
from src.services.runtime_context import runtime_datetime_context
from src.services.tasks import TaskCreate, TaskService

logger = get_logger(__name__)


@dataclass(frozen=True)
class WorkspaceEvent:
    channel: str
    sender: str
    text: str
    target: str = ""
    status: str = "INFO"
    task_id: int | None = None
    sender_agent_id: str = ""


@dataclass(frozen=True)
class TelegramContext:
    telegram_user_id: int
    chat_id: int
    text: str
    source_agent_id: str = "chief"
    event_sink: Callable[[WorkspaceEvent], Awaitable[None]] | None = None


@dataclass(frozen=True)
class OrchestrationResult:
    reply: str
    agent_id: str
    agent_name: str
    task_id: int | None = None
    delegated_to: str | None = None


@dataclass(frozen=True)
class AgentExecutionResult:
    agent: AgentDefinition
    task_id: int
    ok: bool
    response: str
    error: str = ""


@dataclass(frozen=True)
class AgentTrigger:
    agent_id: str
    text: str
    explicit: bool


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _contains_any(text: str, keywords: list[str]) -> bool:
    low = text.lower()
    return any(keyword in low for keyword in keywords)


AGENT_TRIGGERS: dict[str, tuple[str, ...]] = {
    "chief": ("chief", "\u0447\u0438\u0444"),
    "business": ("business", "\u0431\u0438\u0437\u043d\u0435\u0441"),
    "smm": ("smm", "\u0441\u043c\u043c"),
}


ROBOTIC_LABELS = (
    "next",
    "summary",
    "recommendation",
    "status",
    "goal",
    "result",
    "owner",
    "priority",
    "\u0443\u0433\u043e\u043b",
    "\u0433\u043b\u0430\u0432\u043d\u043e\u0435",
    "\u0440\u0438\u0441\u043a",
    "\u0434\u0430\u043b\u044c\u0448\u0435",
    "\u0432\u044b\u0432\u043e\u0434",
    "\u043f\u0440\u0438\u043e\u0440\u0438\u0442\u0435\u0442",
)


CODING_ONLY_REFUSAL_PATTERNS = (
    "i'm a coding-focused assistant",
    "i am a coding-focused assistant",
    "i can only help with software development",
    "only help with software development and technical topics",
    "what would you like to build or debug",
)


def _is_coding_only_refusal(text: str) -> bool:
    low = (text or "").lower()
    return any(pattern in low for pattern in CODING_ONLY_REFUSAL_PATTERNS)


def detect_agent_trigger(text: str) -> AgentTrigger | None:
    clean = (text or "").strip()
    for agent_id, aliases in AGENT_TRIGGERS.items():
        for alias in aliases:
            pattern = rf"^\s*{re.escape(alias)}\s*[,.:;\-?]?\s+(.+)$"
            match = re.match(pattern, clean, flags=re.IGNORECASE)
            if match:
                return AgentTrigger(agent_id=agent_id, text=match.group(1).strip(), explicit=True)
    return None


def strip_robotic_labels(text: str) -> str:
    lines: list[str] = []
    for line in (text or "").splitlines():
        stripped = line.strip()
        if ":" in stripped:
            label, value = stripped.split(":", 1)
            if label.strip().lower() in ROBOTIC_LABELS and value.strip():
                lines.append(value.strip())
                continue
        lines.append(line)
    return "\n".join(lines).strip()


def _wants_detailed_response(text: str) -> bool:
    return _contains_any(
        text,
        [
            "подробно",
            "детально",
            "развернуто",
            "разверни",
            "полный план",
            "deep dive",
            "detail",
            "long",
        ],
    )


def detect_memory_command(text: str) -> tuple[str, str] | None:
    clean = _norm(text)
    low = clean.lower()
    recall_phrases = (
        "что ты помнишь обо мне",
        "что помнишь обо мне",
        "что ты знаешь обо мне",
        "покажи память",
        "моя память",
    )
    if any(phrase in low for phrase in recall_phrases):
        return ("recall", "")

    remember_patterns = (
        r"^запомни это[:,]?\s*(.+)$",
        r"^запомни[:,]?\s*(.+)$",
        r"^remember this[:,]?\s*(.+)$",
    )
    for pattern in remember_patterns:
        match = re.match(pattern, clean, flags=re.IGNORECASE)
        if match and match.group(1).strip():
            return ("remember", match.group(1).strip())

    forget_patterns = (
        r"^забудь это[:,]?\s*(.+)$",
        r"^забудь[:,]?\s*(.+)$",
        r"^forget this[:,]?\s*(.+)$",
    )
    for pattern in forget_patterns:
        match = re.match(pattern, clean, flags=re.IGNORECASE)
        if match and match.group(1).strip():
            return ("forget", match.group(1).strip())
    return None


def is_explicit_coding_request(text: str) -> bool:
    low = _norm(text).lower()
    coding_markers = (
        "напиши код",
        "код для",
        "python скрипт",
        "скрипт на python",
        "исправь fastapi",
        "fastapi ошиб",
        "debug",
        "баг в коде",
        "ошибка в коде",
        "javascript",
        "typescript",
        "react",
        "sqlalchemy",
        "docker",
        "git ",
        "backend",
        "frontend",
        "api endpoint",
        "программа на",
        "программирован",
        "разработка",
    )
    return any(marker in low for marker in coding_markers)


def detect_user_task_title(text: str) -> str | None:
    clean = _norm(text)
    low = clean.lower()
    task_phrases = [
        "добавь задачу",
        "создай задачу",
        "запиши задачу",
        "поставь задачу",
        "задача:",
        "add task",
        "create task",
    ]
    if any(phrase in low for phrase in task_phrases):
        title = _norm(
            re.sub(
                r".*(добавь задачу|создай задачу|запиши задачу|поставь задачу|задача:|add task|create task)",
                "",
                clean,
                flags=re.IGNORECASE,
            )
        )
        return title[:200] or None
    if low.startswith("надо ") or low.startswith("нужно "):
        return _norm(re.sub(r"^(надо|нужно)\s+", "", clean, flags=re.IGNORECASE))[:200] or None
    return None


def is_active_task_question(text: str) -> bool:
    low = _norm(text).lower()
    task_words = ["задач", "дела", "что делать", "план на сегодня", "что у меня", "tasks", "todo"]
    today_words = ["сегодня", "сейчас", "актив", "актуальн", "нужно сделать", "today", "active"]
    completed_words = ["выполн", "сделал", "готово", "completed", "done", "уже сделал"]
    return (
        any(word in low for word in task_words)
        and any(word in low for word in today_words)
        and not any(word in low for word in completed_words)
    )


def is_today_guidance_question(text: str) -> bool:
    low = _norm(text).lower()
    return any(
        phrase in low
        for phrase in [
            "что мне делать сегодня",
            "что делать сегодня",
            "чем заняться сегодня",
            "что посоветуешь на сегодня",
            "что сегодня сделать",
            "какой план на сегодня",
            "собери план на сегодня",
        ]
    )


def is_agent_status_question(text: str) -> bool:
    low = _norm(text).lower()
    return any(
        phrase in low
        for phrase in [
            "что ты сейчас делаешь",
            "над чем ты работаешь",
            "что делает chief",
            "чем занят",
            "что делают агенты",
            "agent status",
        ]
    )


def lightweight_reply(text: str, agent: AgentDefinition) -> str | None:
    low = _norm(text).lower()
    if low in {"ping", "/ping"}:
        return f"{agent.name}: pong"
    if low in {"health", "/health", "status"}:
        return f"{agent.name} на связи. Polling работает."
    if low in {"привет", "hello", "hi", "/hello"}:
        if agent.id == "chief":
            return "Я на связи. Можешь кидать задачу."
        if agent.id == "business":
            return "Business online. Могу быстро разобрать идею, деньги, рынок и MVP."
        if agent.id == "smm":
            return "SMM online. Могу собрать хуки, посты, контент-план и growth-механику."
    return None


class AgentOrchestrator:
    def __init__(self, registry: AgentRegistry | None = None) -> None:
        self.registry = registry or AgentRegistry()
        self.tasks = TaskService()
        self.live_info = LiveInfoService()
        self.maps = YandexMapsService()
        self.memory = MemoryService()
        self.goals = GoalsPrioritiesService()
        self.action_engine = ActionEngineService()

    async def create_user_task(self, user_id: int, title: str, source: str) -> Task:
        task = await self.tasks.create(
            TaskCreate(
                user_id=user_id,
                title=title,
                task_type="user_task",
                status="active",
                assigned_agent=self.registry.get("chief").name,
                created_by="user",
                source=source,
                current_step="Waiting for CEO",
                action_log=f"Created from {source}",
            )
        )
        await self._record_agent_message(
            task_id=task.id,
            from_agent="CEO",
            to_agent="Chief",
            channel="task_created",
            content=f"User task captured from {source}: {title}",
        )
        logger.info("user task created", task_id=task.id, source=source)
        return task

    async def active_tasks_reply(self, user_id: int) -> str:
        tasks = await self.tasks.list_open(user_id=user_id, task_type="user_task", limit=20)
        if not tasks:
            return "Статус: активных пользовательских задач на сегодня нет."
        lines = ["Статус: активные пользовательские задачи", ""]
        lines.extend([f"- #{task.id} — {task.title}" for task in tasks])
        return "\n".join(lines)

    async def agent_status_reply(self, user_id: int) -> str:
        tasks = await self.tasks.list_open(user_id=user_id, task_type="agent_task", limit=10)
        if not tasks:
            return "Статус: активной работы агентов нет.\nРежим: calm background."
        lines = ["Статус: активная работа агентов", ""]
        lines.extend([f"- #{task.id} — {task.assigned_to}: {task.title} ({task.status})" for task in tasks])
        return "\n".join(lines)

    async def handle_telegram_message(self, context: TelegramContext) -> OrchestrationResult:
        settings = get_settings()
        user_id = settings.web_owner_id
        text = _norm(context.text)
        original_source_agent_id = context.source_agent_id
        source_agent = self.registry.get(context.source_agent_id)
        trigger = detect_agent_trigger(text)
        if trigger is not None:
            text = _norm(trigger.text)
            source_agent = self.registry.get(trigger.agent_id)
        expired = await self.tasks.expire_stale_agent_tasks(user_id=user_id, older_than_seconds=max(settings.agent_task_timeout_seconds * 4, 300))
        if expired:
            logger.info("stale agent tasks expired", count=expired, user_id=user_id)

        quick = lightweight_reply(text, source_agent)
        if quick:
            logger.info("agent selected", selected_agent=source_agent.id, reason="lightweight_command")
            return OrchestrationResult(reply=quick, agent_id=source_agent.id, agent_name=source_agent.name)

        memory_command = detect_memory_command(text)
        if memory_command:
            action, value = memory_command
            logger.info("agent selected", selected_agent="chief", reason=f"memory_{action}")
            if action == "recall":
                return OrchestrationResult(reply=await self.memory.recall_reply(user_id), agent_id="chief", agent_name="Chief")
            if action == "remember":
                await self.memory.remember(user_id, value, category="manual", importance=4)
                return OrchestrationResult(reply="Запомнил.", agent_id="chief", agent_name="Chief")
            if action == "forget":
                deleted = await self.memory.forget(user_id, value)
                reply = "Забыл." if deleted else "Не нашёл такую память."
                return OrchestrationResult(reply=reply, agent_id="chief", agent_name="Chief")

        task_title = detect_user_task_title(text)
        if task_title:
            logger.info("agent selected", selected_agent="chief", reason="user_task_capture")
            task = await self.create_user_task(user_id, task_title, "telegram")
            return OrchestrationResult(
                reply=f"Статус: задача добавлена в workspace.\nID: #{task.id}\nЗадача: {task.title}",
                agent_id="chief",
                agent_name="Chief",
                task_id=task.id,
            )

        if is_today_guidance_question(text):
            logger.info("agent selected", selected_agent="chief", reason="today_guidance")
            return await self._handle_direct_agent_message(user_id, self.registry.get("chief"), text, context.chat_id, context.event_sink)

        if is_active_task_question(text):
            logger.info("agent selected", selected_agent="chief", reason="active_task_question")
            return OrchestrationResult(reply=await self.active_tasks_reply(user_id), agent_id="chief", agent_name="Chief")

        if is_agent_status_question(text):
            logger.info("agent selected", selected_agent="chief", reason="agent_status_question")
            return OrchestrationResult(reply=await self.agent_status_reply(user_id), agent_id="chief", agent_name="Chief")

        if trigger is not None and trigger.agent_id == "chief":
            logger.info("agent selected", selected_agent="chief", reason="explicit_trigger")
            return await self._handle_direct_agent_message(user_id, source_agent, text, context.chat_id, context.event_sink)

        if trigger is not None and trigger.agent_id != "chief":
            if original_source_agent_id == trigger.agent_id:
                logger.info("agent selected", selected_agent=source_agent.id, reason="direct_private_agent_trigger")
                return await self._handle_direct_agent_message(user_id, source_agent, text, context.chat_id, context.event_sink)
            if original_source_agent_id != "chief":
                logger.info(
                    "agent ignored message addressed to another agent",
                    source_agent=original_source_agent_id,
                    target_agent=trigger.agent_id,
                )
                return OrchestrationResult(reply="", agent_id=original_source_agent_id, agent_name=self.registry.get(original_source_agent_id).name)
            logger.info("agent selected", selected_agent=source_agent.id, reason="explicit_delegation_trigger")
            return await self._handle_chief_delegation(user_id, text, context.chat_id, [source_agent], context.event_sink)

        if source_agent.id != "chief":
            logger.info("agent selected", selected_agent=source_agent.id, reason="private_agent_chat")
            return await self._handle_direct_agent_message(user_id, source_agent, text, context.chat_id, context.event_sink)

        delegate, reason = self._select_delegate_agent(text)
        if delegate is not None:
            logger.info("agent selected", selected_agent=delegate.id, reason=reason)
            return await self._handle_chief_delegation(user_id, text, context.chat_id, [delegate], context.event_sink)
        if is_explicit_coding_request(text):
            logger.info("agent selected", selected_agent="chief", reason="coding_request_no_coding_agent_configured")
        else:
            logger.info("agent selected", selected_agent="chief", reason=reason)
        return await self._handle_direct_agent_message(user_id, source_agent, text, context.chat_id, context.event_sink)

    async def _handle_direct_agent_message(
        self,
        user_id: int,
        agent: AgentDefinition,
        text: str,
        chat_id: int,
        event_sink: Callable[[WorkspaceEvent], Awaitable[None]] | None,
    ) -> OrchestrationResult:
        task = await self._create_agent_task(user_id, agent, text, chat_id, status="active")
        await self._record_agent_message(task.id, "CEO", agent.name, "request", f"Task received by {agent.name}: {text[:600]}")
        await self._emit(event_sink, WorkspaceEvent("tasks", agent.name, self._short_goal(text), target=agent.name, status="IN_PROGRESS", task_id=task.id, sender_agent_id=agent.id))
        logger.info("task received", task_id=task.id, agent=agent.id, mode="direct")
        await self.tasks.set_status(task.id, "in_progress", f"{agent.name} is working", "task started")
        logger.info("task started", task_id=task.id, agent=agent.id, generated_by=agent.id)
        try:
            reply = await self._generate_agent_reply(user_id, agent, text, persist_chat=True)
        except Exception as exc:
            logger.exception("task generation failed; attempting partial reply", task_id=task.id, agent=agent.id)
            reply = await self._partial_fallback_reply(agent, text)
            await self.tasks.set_status(
                task.id,
                "completed",
                "Partial",
                f"{agent.name} returned partial data after generation failure",
                result=reply,
            )
            await self._record_agent_message(task.id, agent.name, "CEO", "partial_result", reply[:1200])
            await self._emit(
                event_sink,
                WorkspaceEvent(
                    "infra",
                    "SYSTEM",
                    f"{agent.name} вернул частичный ответ после сбоя генерации.",
                    status="PARTIAL",
                    task_id=task.id,
                    sender_agent_id="chief",
                ),
            )
            return OrchestrationResult(reply=reply, agent_id=agent.id, agent_name=agent.name, task_id=task.id)

        await self.tasks.set_status(task.id, "completed", "Completed", f"{agent.name} delivered response", result=reply)
        await self._record_agent_message(task.id, agent.name, "CEO", "result", reply[:1200])
        await self._emit(event_sink, WorkspaceEvent("tasks", agent.name, "result ready", target="Chief", status="DONE", task_id=task.id, sender_agent_id=agent.id))
        logger.info("task completed", task_id=task.id, agent=agent.id, generated_by=agent.id)
        return OrchestrationResult(reply=reply, agent_id=agent.id, agent_name=agent.name, task_id=task.id)

    async def _handle_chief_delegation(
        self,
        user_id: int,
        text: str,
        chat_id: int,
        delegates: list[AgentDefinition],
        event_sink: Callable[[WorkspaceEvent], Awaitable[None]] | None,
    ) -> OrchestrationResult:
        chief = self.registry.get("chief")
        parent = await self._create_agent_task(user_id, chief, text, chat_id, status="in_progress")
        await self._record_agent_message(parent.id, "CEO", "Chief", "request", f"Incoming request: {text[:600]}")
        await self._record_agent_message(
            parent.id,
            "Chief",
            ", ".join(agent.name for agent in delegates),
            "dispatch",
            f"Chief dispatched execution to: {', '.join(agent.name for agent in delegates)}",
        )
        await self._emit(
            event_sink,
            WorkspaceEvent("tasks", "Chief", self._short_goal(text), target=", ".join(agent.name for agent in delegates), status="DISPATCHED", task_id=parent.id, sender_agent_id="chief"),
        )
        logger.info(
            "task dispatched",
            task_id=parent.id,
            from_agent="chief",
            to_agents=[agent.id for agent in delegates],
        )

        executions = [
            self._execute_delegate(user_id=user_id, parent_task_id=parent.id, agent=agent, text=text, chat_id=chat_id, event_sink=event_sink)
            for agent in delegates
        ]
        gathered = await asyncio.gather(*executions, return_exceptions=True)
        results: list[AgentExecutionResult] = []
        for index, item in enumerate(gathered):
            if isinstance(item, AgentExecutionResult):
                results.append(item)
                continue
            agent = delegates[index]
            logger.error(
                "delegated worker crashed before result",
                task_id=parent.id,
                agent=agent.id,
                error=item.__class__.__name__ if isinstance(item, BaseException) else str(item),
            )
            results.append(
                AgentExecutionResult(
                    agent=agent,
                    task_id=parent.id,
                    ok=False,
                    response=f"{agent.name} did not finish this step",
                    error=f"{agent.name} did not finish this step",
                )
            )
        ok_results = [result for result in results if result.ok]
        failed_results = [result for result in results if not result.ok]
        parent_result = "\n\n".join([f"{result.agent.name}: {result.response}" for result in results])
        await self.tasks.set_status(parent.id, "completed", "Completed", "Workers published their results", result=parent_result)
        await self._record_agent_message(parent.id, "Chief", "CEO", "result", "Workers published results directly in GENERAL")
        if failed_results:
            names = ", ".join(result.agent.name for result in failed_results)
            await self._emit(event_sink, WorkspaceEvent("general", "Chief", f"{names} не ответил. Перезапускаю задачу позже.", status="FAILED", task_id=parent.id, sender_agent_id="chief"))
        logger.info("delegated workers completed", task_id=parent.id, ok=[r.agent.id for r in ok_results], failed=[r.agent.id for r in failed_results])
        return OrchestrationResult(
            reply="",
            agent_id="chief",
            agent_name=chief.name,
            task_id=parent.id,
            delegated_to=", ".join(agent.name for agent in delegates),
        )

    async def _execute_delegate(
        self,
        user_id: int,
        parent_task_id: int,
        agent: AgentDefinition,
        text: str,
        chat_id: int,
        event_sink: Callable[[WorkspaceEvent], Awaitable[None]] | None,
    ) -> AgentExecutionResult:
        settings = get_settings()
        task = await self._create_agent_task(
            user_id,
            agent,
            text,
            chat_id,
            status="delegated",
            context=f"parent_task_id={parent_task_id}; telegram_chat_id={chat_id}",
        )
        await self._record_agent_message(parent_task_id, "Chief", agent.name, "task_dispatched", f"Delegated task #{task.id}: {text[:600]}")
        await self._record_agent_message(task.id, "Chief", agent.name, "task_received", f"{agent.name} received delegated task from Chief")
        await self._emit(event_sink, WorkspaceEvent("tasks", "Chief", self._short_goal(text), target=agent.name, status="DELEGATED", task_id=task.id, sender_agent_id="chief"))
        logger.info("task received", task_id=task.id, parent_task_id=parent_task_id, agent=agent.id)

        await self.tasks.set_status(task.id, "in_progress", f"{agent.name} is working", "task started")
        await self._record_agent_message(task.id, agent.name, "Chief", "task_started", f"{agent.name} started execution")
        await self._emit(event_sink, WorkspaceEvent("tasks", agent.name, "accepted", target="Chief", status="IN_PROGRESS", task_id=task.id, sender_agent_id=agent.id))
        await self._emit(event_sink, WorkspaceEvent("general", agent.name, self._worker_ack_line(agent), target="Chief", status="IN_PROGRESS", task_id=task.id, sender_agent_id=agent.id))
        logger.info("task started", task_id=task.id, parent_task_id=parent_task_id, agent=agent.id, generated_by=agent.id)

        delegate_prompt = self._build_delegate_prompt(agent, text)
        response: str | None = None
        last_error = ""
        for attempt in range(2):
            try:
                response = await asyncio.wait_for(
                    self._generate_agent_reply(user_id, agent, delegate_prompt, persist_chat=False),
                    timeout=settings.agent_task_timeout_seconds,
                )
                break
            except asyncio.TimeoutError:
                last_error = f"{agent.name} agent timeout after {settings.agent_task_timeout_seconds}s"
                await self._record_agent_message(task.id, agent.name, "Chief", "timeout", last_error)
                await self._emit(
                    event_sink,
                    WorkspaceEvent(
                        "infra",
                        "SYSTEM",
                        f"{agent.name} не ответил вовремя. Пробую ещё раз.",
                        status="TIMEOUT",
                        task_id=task.id,
                        sender_agent_id="chief",
                    ),
                )
                logger.warning("task timeout", task_id=task.id, parent_task_id=parent_task_id, agent=agent.id, retry=attempt + 1)
                if attempt == 0:
                    await self._emit(
                        event_sink,
                        WorkspaceEvent(
                            "general",
                            "Chief",
                            f"{agent.name} не ответил. Перезапускаю задачу.",
                            status="RETRY",
                            task_id=task.id,
                            sender_agent_id="chief",
                        ),
                    )
                    continue
            except Exception as exc:
                last_error = f"{agent.name} did not finish this step"
                await self._record_agent_message(task.id, agent.name, "Chief", "failed", last_error)
                await self._emit(
                    event_sink,
                    WorkspaceEvent(
                        "infra",
                        "SYSTEM",
                        f"{agent.name} не завершил шаг. Продолжаю с частичными данными.",
                        status="FAILED",
                        task_id=task.id,
                        sender_agent_id="chief",
                    ),
                )
                logger.exception("task failed", task_id=task.id, parent_task_id=parent_task_id, agent=agent.id)
                break

        if response is None:
            error = last_error or f"{agent.name} agent unavailable"
            await self.tasks.set_status(task.id, "completed", "Failed", error, result=error)
            return AgentExecutionResult(agent=agent, task_id=task.id, ok=False, response=error, error=error)

        await self.tasks.set_status(task.id, "completed", "Completed", f"{agent.name} completed delegated result", result=response)
        await self._record_agent_message(task.id, agent.name, "Chief", "task_completed", response[:1200])
        await self._record_agent_message(parent_task_id, agent.name, "Chief", "task_completed", f"Task #{task.id} completed: {response[:1000]}")
        await self._emit(event_sink, WorkspaceEvent("tasks", agent.name, "result ready", target="Chief", status="DONE", task_id=task.id, sender_agent_id=agent.id))
        published = await self._emit(event_sink, WorkspaceEvent("general", agent.name, response, target="Chief", status="DONE", task_id=task.id, sender_agent_id=agent.id))
        if not published:
            await self._emit(
                event_sink,
                WorkspaceEvent(
                    "infra",
                    "SYSTEM",
                    f"{agent.name} подготовил ответ, но его не удалось отправить в общий чат.",
                    status="FAILED",
                    task_id=task.id,
                    sender_agent_id="chief",
                ),
            )
        logger.info("task completed", task_id=task.id, parent_task_id=parent_task_id, agent=agent.id, generated_by=agent.id)
        return AgentExecutionResult(agent=agent, task_id=task.id, ok=True, response=response)

    async def _partial_fallback_reply(self, agent: AgentDefinition, text: str) -> str:
        chunks = [
            "Не удалось получить часть данных по маршруту/генерации. Показываю то, что удалось найти."
        ]
        try:
            live_context = await self._live_context(agent, text)
        except Exception as exc:
            logger.warning("partial fallback live context failed", agent=agent.id, error=exc.__class__.__name__)
            live_context = ""

        if live_context:
            chunks.append(self._humanize_context_for_partial_reply(live_context))
        else:
            chunks.append("Сейчас есть сбой в одном из источников данных. Лучше повторить запрос через минуту.")
        return "\n\n".join(chunk for chunk in chunks if chunk.strip()).strip()

    def _humanize_context_for_partial_reply(self, context: str) -> str:
        lines: list[str] = []
        for raw in context.splitlines():
            line = raw.strip()
            if not line:
                continue
            if line.startswith(("Web search context:", "Yandex Maps context:", "Current weather NOW context:", "City local time context:")):
                continue
            if line.startswith("Direct answer:"):
                lines.append(line.replace("Direct answer:", "").strip())
                continue
            if line.startswith(("From:", "To:", "Straight-line distance:", "car:", "walk:", "Location:", "Observed at:", "Temperature:", "Feels like:", "Conditions:", "Humidity:", "Wind:")):
                lines.append(line)
                continue
            if re.match(r"^\d+\.\s+", line):
                cleaned = re.sub(r"\s+-\s+Source:\s+.+$", "", line)
                lines.append(cleaned)
            if len(lines) >= 10:
                break
        return "\n".join(lines[:10]).strip() or "Есть частичные данные, но не получилось безопасно собрать короткую сводку."

    def _select_delegate_agent(self, text: str) -> tuple[AgentDefinition | None, str]:
        low = text.lower()
        asks_to_delegate = _contains_any(low, ["делегируй", "передай", "пусть", "подключи", "назначь"])
        if asks_to_delegate and _contains_any(low, ["smm", "смм"]):
            return self.registry.get("smm"), "explicit_delegate_smm"
        if asks_to_delegate and _contains_any(low, ["business", "бизнес"]):
            return self.registry.get("business"), "explicit_delegate_business"
        return None, "general_assistant"

    async def _create_agent_task(
        self,
        user_id: int,
        agent: AgentDefinition,
        text: str,
        chat_id: int,
        status: str,
        context: str = "",
    ) -> Task:
        return await self.tasks.create(
            TaskCreate(
                user_id=user_id,
                title=self._agent_task_title(agent, text),
                status=status,
                task_type="agent_task",
                assigned_agent=agent.name,
                created_by="Chief" if agent.id != "chief" else "CEO",
                source="telegram",
                description=text[:600],
                context=context or f"telegram_chat_id={chat_id}",
                current_step=f"{agent.name} is understanding request",
                action_log=f"Created from Telegram chat {chat_id}\nRouted to {agent.name}",
            )
        )

    def _agent_task_title(self, agent: AgentDefinition, text: str) -> str:
        clean = _norm(text).rstrip(".!?")
        if agent.id == "business":
            return f"Business strategy: {clean[:170]}"
        if agent.id == "smm":
            return f"SMM execution: {clean[:180]}"
        return f"Chief orchestration: {clean[:180]}"

    async def _record_agent_message(self, task_id: int, from_agent: str, to_agent: str, channel: str, content: str) -> None:
        async with AsyncSessionLocal() as session:
            session.add(
                AgentMessage(
                    task_id=task_id,
                    from_agent=from_agent,
                    to_agent=to_agent,
                    channel=channel,
                    content=content,
                    status="delivered",
                )
            )
            await session.commit()

    async def _emit(
        self,
        event_sink: Callable[[WorkspaceEvent], Awaitable[None]] | None,
        event: WorkspaceEvent,
    ) -> bool:
        if event_sink is None:
            return False
        try:
            await event_sink(event)
            return True
        except Exception:
            logger.exception(
                "Telegram workspace event publish failed",
                channel=event.channel,
                sender=event.sender,
                task_id=event.task_id,
            )
            return False

    def _short_goal(self, text: str, limit: int = 90) -> str:
        clean = _norm(text)
        clean = re.sub(r"^(подробно|детально|разверни|сделай|придумай|оцени)\s+", "", clean, flags=re.IGNORECASE)
        if len(clean) <= limit:
            return clean
        cut = clean[:limit].rsplit(" ", 1)[0].strip()
        return f"{cut}..."

    def _worker_ack_line(self, agent: AgentDefinition) -> str:
        if agent.id == "smm":
            return "\u041f\u0440\u0438\u043d\u044f\u043b. \u0421\u043c\u043e\u0442\u0440\u044e hooks \u0438 CTA."
        if agent.id == "business":
            return "\u041f\u0440\u0438\u043d\u044f\u043b. \u0421\u043c\u043e\u0442\u0440\u044e MVP \u0438 \u0440\u0438\u0441\u043a\u0438."
        return "\u041f\u0440\u0438\u043d\u044f\u043b. \u0420\u0430\u0431\u043e\u0442\u0430\u044e."

    def _build_delegate_prompt(self, agent: AgentDefinition, text: str) -> str:
        return (
            f"Internal delegated task from Chief to {agent.name}.\n\n"
            f"Original CEO request:\n{text}\n\n"
            "Return only your short specialist result for the team chat.\n"
            "Default length: 5-8 lines.\n"
            "Do not repeat the CEO request.\n"
            "Do not write an essay.\n"
            "Do not say that you passed the result to Chief.\n"
            "Do not include Owner, Status, Priority, Summary, or Task ID labels.\n"
            "Do not use markdown tables.\n"
            "Use **bold** only for the strongest insight, risk, or decision. Do not over-format.\n"
            "Do not bold service words like accepted, ready, status, done, or received.\n"
            "Use natural Telegram team language, not backend-log labels.\n"
        )

    def _build_aggregation_input(self, original_text: str, results: list[AgentExecutionResult]) -> str:
        blocks = []
        for result in results:
            status = "ok" if result.ok else "timeout/unavailable"
            blocks.append(
                f"Agent: {result.agent.name}\n"
                f"Task ID: #{result.task_id}\n"
                f"Agent state: {status}\n"
                f"Result:\n{result.response}"
            )
        return (
            "Chief aggregation task.\n\n"
            f"Original CEO request:\n{original_text}\n\n"
            "Specialist results:\n\n"
            + "\n\n---\n\n".join(blocks)
            + "\n\nNow produce the final executive summary for CEO.\n"
            "Use Russian unless the CEO asked otherwise.\n"
            "Tone: operator/executive, short and confident.\n"
            "Default length: 5-10 lines.\n"
            "Do not repeat the original request.\n"
            "Do not write a giant summary.\n"
            "Use natural Telegram prose, not rigid section labels.\n"
            "Do not use markdown tables, pipe layouts, ASCII tables, or raw ## headings.\n"
            "End with a complete human sentence, not a system label.\n"
            "Never address yourself as Chief. Never say 'Принято, Chief'."
        )

    async def _generate_agent_reply(
        self,
        user_id: int,
        agent: AgentDefinition,
        text: str,
        *,
        persist_chat: bool,
    ) -> str:
        settings = get_settings()
        async with AsyncSessionLocal() as session:
            if persist_chat:
                session.add(ChatMessage(user_id=user_id, role="user", content=text))
                await session.commit()
            history = list(
                reversed(
                    (
                        await session.execute(
                            select(ChatMessage)
                            .where(ChatMessage.user_id == user_id)
                            .order_by(ChatMessage.created_at.desc())
                            .limit(settings.max_history_messages)
                        )
                    )
                    .scalars()
                    .all()
                )
            )
            history = [item for item in history if not _is_coding_only_refusal(item.content)]
            profile = await session.get(UserProfile, user_id)
            tasks = await self.tasks.list_open(user_id=user_id, task_type="user_task", limit=10)
            agent_tasks = await self.tasks.list_open(user_id=user_id, task_type="agent_task", limit=8)
            plans = (
                await session.execute(
                    select(DayPlanItem)
                    .where(DayPlanItem.user_id == user_id, DayPlanItem.plan_date == date.today(), DayPlanItem.status == "active")
                    .order_by(DayPlanItem.id.asc())
                    .limit(10)
                )
            ).scalars().all()
            reminders = (
                await session.execute(
                    select(Reminder)
                    .where(Reminder.user_id == user_id, Reminder.status == "active")
                    .order_by(Reminder.remind_at.asc())
                    .limit(5)
                )
            ).scalars().all()
            shared_messages = (
                await session.execute(
                    select(AgentMessage)
                    .order_by(AgentMessage.created_at.desc(), AgentMessage.id.desc())
                    .limit(15)
                )
            ).scalars().all()

        memory_context = await self.memory.context_for_user(user_id)
        goals_context = await self.goals.context_for_prompt()
        action_engine_context = await self.action_engine.context_for_prompt()
        live_context = await self._live_context(agent, text)
        system_prompt = self._build_agent_system_prompt(
            agent,
            profile,
            memory_context,
            goals_context,
            action_engine_context,
            tasks,
            agent_tasks,
            plans,
            reminders,
            list(reversed(shared_messages)),
            live_context,
        )
        messages = [{"role": item.role, "content": item.content} for item in history if item.role in {"user", "assistant"}]
        if not persist_chat:
            messages.append({"role": "user", "content": text})

        logger.info("agent generation started", agent=agent.id, generated_by=agent.id, persist_chat=persist_chat)
        requested_detail = _wants_detailed_response(text)
        max_tokens = max(settings.claude_max_tokens, 1200) if requested_detail else min(max(settings.claude_max_tokens, 420), 650)
        reply = await ClaudeService().generate_response(
            system_prompt=system_prompt,
            history_messages=messages,
            max_tokens=max_tokens,
        )
        if _is_coding_only_refusal(reply):
            logger.error("coding-only refusal returned by model; retrying with sanitized Chief context", agent=agent.id)
            retry_prompt = (
                system_prompt
                + "\n\nCritical correction:\n"
                "- You are Chief, Artem's personal AI agent, not a coding-only assistant.\n"
                "- Answer the user's request directly using Chief persona, long-term memory, and available live context.\n"
                "- Do not mention software-only limitations."
            )
            reply = await ClaudeService().generate_response(
                system_prompt=retry_prompt,
                history_messages=[{"role": "user", "content": text}],
                max_tokens=max_tokens,
            )
            if _is_coding_only_refusal(reply):
                logger.error("coding-only refusal persisted after retry; returning safe Chief fallback", agent=agent.id)
                reply = self._safe_provider_mismatch_reply(agent, text, memory_context, tasks)
        logger.info("agent generation completed", agent=agent.id, generated_by=agent.id, length=len(reply))

        if persist_chat:
            async with AsyncSessionLocal() as session:
                session.add(ChatMessage(user_id=user_id, role="assistant", content=reply))
                await session.commit()
        return self._clean_agent_reply(agent, reply)

    def _safe_provider_mismatch_reply(
        self,
        agent: AgentDefinition,
        text: str,
        memory_context: str,
        tasks: list[Task],
    ) -> str:
        if agent.id != "chief":
            return "Принял. Сейчас не удалось нормально сгенерировать ответ, вернусь к задаче после перезапуска LLM-провайдера."

        low = _norm(text).lower()
        if is_today_guidance_question(text):
            if tasks:
                task_lines = "\n".join(f"• {task.title}" for task in tasks[:5])
                return (
                    "Сегодня я бы не распылялся.\n\n"
                    f"Сначала закрой активные задачи:\n{task_lines}\n\n"
                    "После этого выдели 40 минут на Chief: память, маршрутизацию агентов и один маленький коммерческий сценарий для AI-автоматизации."
                )
            return (
                "Сегодня обязательных задач нет.\n\n"
                "Я бы сделал день вокруг твоих целей:\n"
                "• 40 минут — улучшить память и routing Chief\n"
                "• 30 минут — выписать 5 болей малого бизнеса, которые можно автоматизировать\n"
                "• 30 минут — собрать один простой оффер для Telegram AI Agent\n"
                "• тренировка или прогулка — чтобы не закиснуть за кодом\n\n"
                "Главное — не изучать абстрактно, а собрать один продаваемый сценарий."
            )

        if "иде" in low and "бизнес" in low:
            return (
                "Три идеи под твой фокус на AI-автоматизацию:\n\n"
                "• AI-администратор для маленьких салонов: запись, напоминания, ответы клиентам, возврат потерянных лидов.\n"
                "• Telegram-бот для экспертов: принимает заявки, прогревает, собирает FAQ и ведёт до консультации.\n"
                "• AI-оператор для локального бизнеса: сводит WhatsApp/Telegram/таблицы в одну очередь задач и отчётов.\n\n"
                "Я бы начал со второго: быстрее собрать MVP и проще показать пользу."
            )

        if any(word in low for word in ["погода", "маршрут", "ресторан", "ужин", "доехать", "уфа"]):
            return (
                "Для такого запроса нужен live-слой: поиск, карты и погода.\n\n"
                "Сейчас я не буду выдумывать данные. Проверь LLM-провайдер, и я смогу нормально собрать варианты, дорогу и прогноз одним ответом."
            )

        if "трен" in low or "зал" in low or "сплит" in low:
            return (
                "Для зала держал бы простой сплит:\n\n"
                "• День 1 — грудь, плечи, трицепс\n"
                "• День 2 — спина, бицепс\n"
                "• День 3 — ноги и корпус\n\n"
                "Если времени мало — делай full body 3 раза в неделю и прогрессируй в базовых движениях."
            )

        if memory_context:
            return (
                "Я бы ответил через твой текущий фокус: AI-агенты, автоматизация, бизнес и практическая польза.\n\n"
                "Сейчас лучший следующий шаг — сформулировать задачу как конкретный результат: что нужно получить, для кого и к какому сроку."
            )
        return "Принял. Дай чуть конкретнее цель, и я соберу короткий практический ответ."

    def _build_agent_system_prompt(
        self,
        agent: AgentDefinition,
        profile: UserProfile | None,
        memory_context: str,
        goals_context: str,
        action_engine_context: str,
        tasks: list[Task],
        agent_tasks: list[Task],
        plans: list[DayPlanItem],
        reminders: list[Reminder],
        shared_messages: list[AgentMessage],
        live_context: str = "",
    ) -> str:
        system_prompt = agent.system_prompt.strip()
        system_prompt += (
            "\n\nRuntime rules:\n"
            "- Agent identity comes only from this prompt.\n"
            "- Do not use a generic AI Manager identity.\n"
            "- Do not call yourself by your own name as an addressee.\n"
            "- SQLite tasks table is the single source of truth.\n"
            "- Never call completed/done tasks active.\n"
            "- Telegram is the communication layer; desktop is the orchestration workspace.\n"
            "- Do not use markdown tables, pipe layouts, ASCII tables, or raw ## headings.\n"
            "- Keep Telegram output mobile-readable: short blocks, bullets, clean ending.\n"
            "- Default communication style: short operational update, 5-10 lines maximum.\n"
            "- Only actionable info. Do not repeat the full task context.\n"
            "- Do not produce long summaries unless the user explicitly asks for detail.\n"
            "- Use Russian by default. Avoid English service labels unless the user explicitly writes in English.\n"
            "- Never say that you are coding-focused or that you can only help with software development.\n"
            "- If recent history contains a coding-only refusal, ignore it as corrupted old history.\n"
            "- Use bold only for the strongest insight, risk, or decision. Do not over-format.\n"
            "- Never bold service words like accepted, ready, status, done, or received."
            "\n- Always use Runtime current date/time as the source of truth for today's date, current year, current time, and time-sensitive answers."
            "\n- If the user asks what date/time it is or what date live data refers to, answer from Runtime current date/time and Live information context, not from model memory."
            "\n- If City local time context is available, use it for city/country local time questions."
            "\n- If Current weather NOW context is available, use it for current weather questions; do not answer current weather with a daily average forecast."
            "\n- If the user asks about future weather, use forecast context and be explicit that it is forecast data."
            "\n- Before answering, inspect recent shared context and continue the active topic unless the user explicitly changes it."
            "\n- If another agent already answered the request, do not duplicate the answer."
            "\n- Ignore requests clearly addressed to another agent."
        )
        if agent.id == "chief":
            system_prompt += (
                "\n- Speak like an operations lead: concise, structured, decisive.\n"
                "- You are also the personal AI operator and main assistant layer for the user.\n"
                "- You may answer ordinary requests directly: weather, news, routes, schedules, population, quick research, household questions, advice.\n"
                "- If the user asks what to do today, never stop at 'no active tasks'. If active user tasks exist, prioritize them. If no active tasks exist, use long-term memory, goals, interests, and projects to propose a useful day plan.\n"
                "- Never refuse by saying this is not your role, not your area, or that you are only an orchestrator.\n"
                "- If live information context is available, use it. If it is missing, answer practically and state uncertainty briefly only when needed.\n"
                "- Web search context may contain short Serper results. Use them only as fresh source material; do not mention Serper, Google API, headers, payloads, or debug process.\n"
                "- If web search context says search is unavailable, tell the user briefly: 'Сейчас веб-поиск недоступен.' Then answer only what can be answered safely.\n"
                "- If Yandex Maps context is available, use it for routes, addresses, distance, travel time, traffic, and nearby places.\n"
                "- When answering from maps context, sound human and practical. Do not mention API calls, geocoder, router, payloads, or debug details.\n"
                "- If route origin is missing, ask one short question: from where should I build the route?\n"
                "- Delegate only when Business/SMM expertise is clearly useful; otherwise answer yourself.\n"
                "- Do not use rigid labels like Главное, Риск, Дальше, Вывод, or Приоритет.\n"
                "- Sound like a smart cofounder: confident, concise, human, and slightly pressuring when useful.\n"
                "- Do not write giant executive summaries by default.\n"
                "- Never write 'Принято, Chief' or address yourself as Chief."
                "\n- Use Goals & Priorities as the user's strategic direction, not as casual chat memory."
                "\n- If you give an important idea, turn it into a concrete next action."
                "\n- Prefer practical validation steps over theory."
                "\n- If a topic is in ignore_topics, only discuss it when the user explicitly asks and keep it practical."
            )
        system_prompt += "\n\n" + runtime_datetime_context()
        if agent.id == "business":
            system_prompt += (
                "\n- Business default style: short natural team-chat answer with one clear recommendation."
                "\n- No theoretical explanations unless explicitly requested."
                "\n- Role lock: you are a worker agent. Do not orchestrate, delegate, or coordinate other agents."
                "\n- Return your own concise result; it may be published by your bot in GENERAL."
                "\n- Do not say that the result was passed to Chief."
            )
        if agent.id == "smm":
            system_prompt += (
                "\n- SMM default style: short natural team-chat answer with hooks or CTA only when useful."
                "\n- Give the short version first. Ask 'Развернуть?' if more detail would help."
                "\n- Role lock: you are a worker agent. Do not orchestrate, delegate, or coordinate other agents."
                "\n- Return your own concise result; it may be published by your bot in GENERAL."
                "\n- Do not say that the result was passed to Chief."
            )
        if profile and profile.profile_text:
            system_prompt += f"\n\nUser profile:\n{profile.profile_text}"
        if goals_context:
            system_prompt += f"\n\nStructured user direction:\n{goals_context}"
        if action_engine_context:
            system_prompt += f"\n\n{action_engine_context}"
        if memory_context:
            system_prompt += f"\n\nLong-term user memory:\n{memory_context}"

        context: list[str] = []
        if tasks:
            context += ["Active user tasks:"] + [f"- #{task.id}: {task.title}" for task in tasks]
        else:
            context += ["Active user tasks: none"]
        if agent_tasks:
            context += ["Active agent work:"] + [f"- #{task.id}: {task.assigned_to}: {task.title} ({task.status})" for task in agent_tasks]
        if plans:
            context += ["Today plan:"] + [f"- {item.title}" for item in plans]
        if reminders:
            context += ["Active reminders:"] + [f"- {reminder.text}" for reminder in reminders]
        if context:
            system_prompt += "\n\nWorkspace context:\n" + "\n".join(context)
        recent_context = self._format_shared_context(shared_messages)
        if recent_context:
            system_prompt += "\n\nRecent shared chat context:\n" + recent_context
        if live_context:
            system_prompt += "\n\nLive information context:\n" + live_context
        return system_prompt

    async def _live_context(self, agent: AgentDefinition, text: str) -> str:
        chunks: list[str] = []
        if agent.id == "chief":
            maps_context = await self.maps.context_for(text)
            if maps_context:
                chunks.append(maps_context)
        live_context = await self.live_info.context_for(text)
        if live_context:
            chunks.append(live_context)
        return "\n\n".join(chunks)

    def _format_shared_context(self, messages: list[AgentMessage]) -> str:
        useful: list[str] = []
        noisy_channels = {"task_received", "task_started", "task_dispatched"}
        for message in messages[-15:]:
            if message.channel in noisy_channels:
                continue
            content = _norm(message.content)
            if not content:
                continue
            content = re.sub(r"Task #\d+\s+completed:\s*", "", content, flags=re.IGNORECASE)
            content = content[:260]
            useful.append(f"- {message.from_agent} -> {message.to_agent}: {content}")
        return "\n".join(useful[-12:])

    def _clean_agent_reply(self, agent: AgentDefinition, reply: str) -> str:
        text = (reply or "").strip()
        if agent.id == "chief":
            text = re.sub(r"(?i)\b\u043f\u0440\u0438\u043d\u044f\u0442\u043e,\s*chief[.!]?\s*", "", text).strip()
            text = re.sub(r"(?i)\bchief,\s*", "", text).strip()
        else:
            text = self._clean_worker_reply(text)
        return self._clean_bold_formatting(strip_robotic_labels(text))

    def _clean_worker_reply(self, text: str) -> str:
        address_pattern = r"(?im)^\s*(\u0430\u0440\u0442[\u0435\u0451]\u043c|ceo)\s*[,!:-]\s*"
        text = re.sub(address_pattern, "", text).strip()
        forbidden_phrases = [
            "\u0434\u0435\u043b\u0435\u0433\u0438\u0440",
            "\u043e\u0440\u0433\u0430\u043d\u0438\u0437\u0443",
            "\u0437\u0430\u043f\u0443\u0441\u043a\u0430\u044e \u0430\u0433\u0435\u043d\u0442\u043e\u0432",
            "\u0441\u043e\u0431\u0438\u0440\u0430\u044e \u0441\u0432\u043e\u0434\u043a",
            "executive summary",
            "\u0436\u0434\u0443 \u043f\u043e\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043d",
            "\u0433\u043e\u0442\u043e\u0432\u043e, \u043f\u0435\u0440\u0435\u0434\u0430\u043b",
            "summary ready",
        ]
        lines = []
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                lines.append(line)
                continue
            low = stripped.lower()
            if any(phrase in low for phrase in forbidden_phrases):
                continue
            lines.append(line)
        cleaned = "\n".join(lines).strip()
        return cleaned or text.strip()

    def _clean_bold_formatting(self, text: str) -> str:
        service_replacements = {
            "**\u041f\u0440\u0438\u043d\u044f\u043b.**": "\u041f\u0440\u0438\u043d\u044f\u043b.",
            "**\u041f\u0440\u0438\u043d\u044f\u043b**": "\u041f\u0440\u0438\u043d\u044f\u043b",
            "**\u0413\u043e\u0442\u043e\u0432\u043e.**": "\u0413\u043e\u0442\u043e\u0432\u043e.",
            "**\u0413\u043e\u0442\u043e\u0432\u043e**": "\u0413\u043e\u0442\u043e\u0432\u043e",
            "**OK**": "OK",
            "**Ok**": "Ok",
            "**??**": "??",
            "**????**": "????",
        }
        for bold, plain in service_replacements.items():
            text = text.replace(bold, plain)
        return text

