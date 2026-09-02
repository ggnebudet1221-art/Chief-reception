from datetime import date, datetime, timedelta, timezone
import re

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import delete, select

from src.api.deps import require_token
from src.core.config import get_settings
from src.infrastructure.db.models.memory import (
    ChatMessage,
    DayPlanItem,
    MemoryItem,
    Reminder,
    Task,
    UserProfile,
)
from src.infrastructure.db.session import AsyncSessionLocal
from src.infrastructure.logging.logger import get_logger
from src.services.agents.registry import AgentRegistry
from src.services.ai.claude_service import ClaudeService
from src.services.tasks import TaskCreate, TaskService

router = APIRouter(tags=["web-chat"], dependencies=[Depends(require_token)])
logger = get_logger(__name__)
task_service = TaskService()


class ChatIn(BaseModel):
    message: str


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


async def _save_chat_message(user_id: int, role: str, content: str) -> None:
    async with AsyncSessionLocal() as session:
        session.add(ChatMessage(user_id=user_id, role=role, content=content))
        await session.commit()


def _parse_reminder_time(text: str) -> tuple[datetime | None, str | None]:
    now = datetime.now()

    m = re.search(r"через\s+(\d+)\s+мин", text, re.IGNORECASE)
    if m:
        dt = now + timedelta(minutes=int(m.group(1)))
        title = re.sub(r".*через\s+\d+\s+мин\w*", "", text, flags=re.IGNORECASE)
        return dt, _norm(title)

    m = re.search(r"через\s+(\d+)\s+час", text, re.IGNORECASE)
    if m:
        dt = now + timedelta(hours=int(m.group(1)))
        title = re.sub(r".*через\s+\d+\s+час\w*", "", text, flags=re.IGNORECASE)
        return dt, _norm(title)

    m = re.search(r"завтра\s+в\s+(\d{1,2}:\d{2})", text, re.IGNORECASE)
    if m:
        h, mi = map(int, m.group(1).split(":"))
        dt = (now + timedelta(days=1)).replace(hour=h, minute=mi, second=0, microsecond=0)
        title = re.sub(r".*завтра\s+в\s+\d{1,2}:\d{2}", "", text, flags=re.IGNORECASE)
        return dt, _norm(title)

    m = re.search(r"сегодня\s+в\s+(\d{1,2}:\d{2})", text, re.IGNORECASE)
    if m:
        h, mi = map(int, m.group(1).split(":"))
        dt = now.replace(hour=h, minute=mi, second=0, microsecond=0)
        if dt <= now:
            dt += timedelta(days=1)
        title = re.sub(r".*сегодня\s+в\s+\d{1,2}:\d{2}", "", text, flags=re.IGNORECASE)
        return dt, _norm(title)

    m = re.search(r"(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})", text)
    if m:
        dt = datetime.strptime(f"{m.group(1)} {m.group(2)}", "%Y-%m-%d %H:%M")
        title = re.sub(r".*\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}", "", text)
        return dt, _norm(title)

    return None, None


def _detect_action(msg: str) -> tuple[str | None, dict]:
    text = _norm(msg)
    low = text.lower()

    if low.startswith("запомни"):
        payload = _norm(re.sub(r"^запомни[:,]?", "", text, flags=re.IGNORECASE))
        return "memory", {"content": payload[:300]}

    if any(k in low for k in ["напомни", "создай напоминание", "поставь напоминание"]):
        dt, title = _parse_reminder_time(text)
        return "reminder", {"remind_at": dt, "text": (title or "")[:200]}

    task_phrases = [
        "добавь задачу",
        "создай задачу",
        "запиши задачу",
        "поставь задачу",
        "задача:",
    ]
    if any(k in low for k in task_phrases):
        title = _norm(
            re.sub(
                r".*(добавь задачу|создай задачу|запиши задачу|поставь задачу|задача:)",
                "",
                text,
                flags=re.IGNORECASE,
            )
        )
        return "task", {"title": title[:200]}

    if "сегодня" in low and "футбол" in low and ("пап" in low or "отец" in low):
        return "task", {"title": "Футбол с папой"}

    if low.startswith("надо ") or low.startswith("нужно "):
        title = _norm(re.sub(r"^(надо|нужно)\s+", "", text, flags=re.IGNORECASE))
        return "task", {"title": title[:200]}

    if any(k in low for k in ["запланируй", "добавь в план", "в план на сегодня"]):
        title = _norm(
            re.sub(r".*(запланируй|добавь в план|в план на сегодня)", "", text, flags=re.IGNORECASE)
        )
        return "plan", {"title": title[:200]}

    return None, {}


def _is_active_task_question(msg: str) -> bool:
    low = _norm(msg).lower()
    task_words = ["задач", "дела", "что делать", "план на сегодня", "что у меня"]
    today_words = ["сегодня", "сейчас", "актив", "актуальн", "нужно сделать"]
    completed_words = ["выполн", "сделал", "готово", "completed", "done", "уже сделал"]
    return (
        any(word in low for word in task_words)
        and any(word in low for word in today_words)
        and not any(word in low for word in completed_words)
    )


def _is_completed_task_question(msg: str) -> bool:
    low = _norm(msg).lower()
    task_words = ["задач", "дела", "что сделал", "что уже"]
    completed_words = ["выполн", "сделал", "готово", "completed", "done", "уже сделал"]
    return any(word in low for word in task_words) and any(word in low for word in completed_words)


def _is_agent_status_question(msg: str) -> bool:
    low = _norm(msg).lower()
    return any(
        phrase in low
        for phrase in [
            "что ты сейчас делаешь",
            "над чем ты работаешь",
            "что делает chief",
            "чем занят",
        ]
    )


def _agent_task_title(msg: str) -> str:
    low = _norm(msg).lower()
    if any(word in low for word in ["австрали", "australia"]):
        return "Explain Australia to CEO"
    if any(phrase in low for phrase in ["план на день", "план дня", "составь план", "day plan", "daily plan"]):
        return "Create daily plan for CEO"
    if any(word in low for word in ["исслед", "research", "иде", "business"]):
        return "Research request for CEO"
    clean = _norm(msg).rstrip(".!?")
    return f"Answer CEO request: {clean[:170]}"


def _should_create_agent_task(msg: str, action: str | None) -> bool:
    if action:
        return False
    low = _norm(msg).lower()
    if _is_active_task_question(low) or _is_completed_task_question(low) or _is_agent_status_question(low):
        return False
    if low.startswith(("привет", "спасибо", "ок", "да", "нет")) and len(low) < 30:
        return False
    return True


async def _active_tasks_reply(owner: int) -> dict | None:
    tasks = await task_service.list_open(user_id=owner, task_type="user_task", limit=20)

    if not tasks:
        reply = "Активных задач на сегодня нет."
    else:
        lines = "\n".join([f"- {task.title}" for task in tasks])
        reply = f"Активные задачи на сегодня:\n{lines}"

    await _save_chat_message(owner, "assistant", reply)
    return {"reply": reply, "source": "sqlite_tasks", "active_count": len(tasks)}


async def _completed_tasks_reply(owner: int) -> dict | None:
    tasks = await task_service.list_completed(user_id=owner, task_type="user_task", limit=20)

    if not tasks:
        reply = "Выполненных задач пока нет."
    else:
        lines = "\n".join([f"- {task.title}" for task in tasks])
        reply = f"Выполненные задачи:\n{lines}"

    await _save_chat_message(owner, "assistant", reply)
    return {"reply": reply, "source": "sqlite_tasks", "completed_count": len(tasks)}


async def _find_active_task_by_title(owner: int, title: str) -> Task | None:
    normalized_title = title.strip().lower()
    if not normalized_title:
        return None

    rows = await task_service.list_open(user_id=owner, task_type="user_task", limit=80)

    return next((task for task in rows if task.title.strip().lower() == normalized_title), None)


async def _agent_status_reply(owner: int) -> dict:
    tasks = await task_service.list_open(user_id=owner, task_type="agent_task", limit=10)

    if not tasks:
        reply = "Сейчас активных задач агента нет. Chief в режиме ожидания."
    else:
        lines = "\n".join([f"- {task.title} — {task.status}" for task in tasks])
        reply = f"Chief сейчас работает над:\n{lines}"

    await _save_chat_message(owner, "assistant", reply)
    return {"reply": reply, "source": "sqlite_agent_tasks", "active_count": len(tasks)}


async def _create_agent_task(owner: int, user_message: str) -> Task:
    task = await task_service.create(
        TaskCreate(
            user_id=owner,
            title=_agent_task_title(user_message),
            status="in_progress",
            task_type="agent_task",
            assigned_agent="Chief",
            created_by="workspace_console",
            source="chat",
            description=user_message[:600],
            current_step="Understanding request",
            action_log="Created from chat request\nChief started thinking",
        )
    )
    return task


async def _update_agent_task(task_id: int, status: str, step: str, log_line: str) -> None:
    normalized = "completed" if status in {"done", "completed", "failed"} else "in_progress"
    await task_service.set_status(task_id, normalized, step, log_line)


def _base_system_prompt() -> str:
    prompt = AgentRegistry().get("chief").system_prompt.strip()
    prompt += (
        "\n\nDesktop local console is optional. Telegram is the primary communication layer. "
        "SQLite tasks table is the single source of truth for task state."
    )
    return prompt


async def _finish_action(owner: int, reply: str, action: str, created: bool) -> dict:
    await _save_chat_message(owner, "assistant", reply)
    return {"reply": reply, "action": action, "created": created}


@router.get("/api/chat/history")
async def chat_history() -> list[dict]:
    owner = get_settings().web_owner_id
    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                select(ChatMessage)
                .where(ChatMessage.user_id == owner)
                .order_by(ChatMessage.created_at.desc())
                .limit(50)
            )
        ).scalars().all()

    rows.reverse()
    return [
        {
            "id": message.id,
            "role": message.role,
            "content": message.content,
            "created_at": message.created_at.isoformat(),
        }
        for message in rows
    ]


@router.delete("/api/chat/history")
async def clear_chat_history() -> dict:
    owner = get_settings().web_owner_id
    async with AsyncSessionLocal() as session:
        await session.execute(delete(ChatMessage).where(ChatMessage.user_id == owner))
        await session.commit()
    return {"ok": True}


@router.post("/api/chat")
async def chat(payload: ChatIn) -> dict:
    settings = get_settings()
    owner = settings.web_owner_id
    text = _norm(payload.message)

    await _save_chat_message(owner, "user", text)

    if _is_agent_status_question(text):
        return await _agent_status_reply(owner)
    if _is_active_task_question(text):
        return await _active_tasks_reply(owner)
    if _is_completed_task_question(text):
        return await _completed_tasks_reply(owner)

    action, data = _detect_action(text)
    if action:
        logger.info("chat_action_detected", action=action, owner=owner)

    if action == "memory":
        if not data["content"]:
            return await _finish_action(owner, "Не вижу текста для памяти.", action, False)
        try:
            async with AsyncSessionLocal() as session:
                memory = MemoryItem(user_id=owner, content=data["content"], category="manual", importance=5)
                session.add(memory)
                await session.commit()
                await session.refresh(memory)
            logger.info("chat_action_db_create_success", action=action, owner=owner, entity_id=memory.id)
            return await _finish_action(owner, f"Память сохранена: {memory.content}", action, True)
        except Exception:
            logger.exception("chat_action_db_create_failed", action=action, owner=owner)
            return await _finish_action(owner, "Не удалось сохранить в память. Попробуйте еще раз.", action, False)

    if action == "reminder":
        if data.get("remind_at") is None:
            return await _finish_action(
                owner,
                "Не понял время. Напиши, например: напомни через 30 минут проверить монтаж.",
                action,
                False,
            )
        if not data.get("text"):
            return await _finish_action(owner, "Укажи текст напоминания.", action, False)
        try:
            async with AsyncSessionLocal() as session:
                reminder = Reminder(
                    user_id=owner,
                    chat_id=owner,
                    text=data["text"],
                    remind_at=data["remind_at"],
                    status="active",
                )
                session.add(reminder)
                await session.commit()
                await session.refresh(reminder)
            logger.info("chat_action_db_create_success", action=action, owner=owner, entity_id=reminder.id)
            return await _finish_action(owner, f"Напоминание создано: {reminder.text}", action, True)
        except Exception:
            logger.exception("chat_action_db_create_failed", action=action, owner=owner)
            return await _finish_action(owner, "Не удалось создать напоминание. Попробуйте еще раз.", action, False)

    if action == "task":
        if not data.get("title"):
            return await _finish_action(owner, "Укажи название задачи.", action, False)
        try:
            existing = await _find_active_task_by_title(owner, data["title"])
            if existing:
                return await _finish_action(owner, f"Задача уже есть в очереди: {existing.title}", action, True)
            task = await task_service.create(
                TaskCreate(
                    user_id=owner,
                    title=data["title"],
                    status="active",
                    task_type="user_task",
                    assigned_agent="Chief",
                    created_by="workspace_console",
                    source="chat",
                    current_step="Waiting for CEO",
                    action_log="Created from chat task intent",
                )
            )
            logger.info("chat_action_db_create_success", action=action, owner=owner, entity_id=task.id)
            return await _finish_action(owner, f"Задача создана: {task.title}", action, True)
        except Exception:
            logger.exception("chat_action_db_create_failed", action=action, owner=owner)
            return await _finish_action(owner, "Не удалось создать задачу. Попробуйте еще раз.", action, False)

    if action == "plan":
        if not data.get("title"):
            return await _finish_action(owner, "Укажи пункт плана.", action, False)
        try:
            async with AsyncSessionLocal() as session:
                plan_item = DayPlanItem(
                    user_id=owner,
                    title=data["title"],
                    status="active",
                    plan_date=date.today(),
                )
                session.add(plan_item)
                await session.commit()
                await session.refresh(plan_item)
            logger.info("chat_action_db_create_success", action=action, owner=owner, entity_id=plan_item.id)
            return await _finish_action(owner, f"Пункт плана создан: {plan_item.title}", action, True)
        except Exception:
            logger.exception("chat_action_db_create_failed", action=action, owner=owner)
            return await _finish_action(owner, "Не удалось создать пункт плана. Попробуйте еще раз.", action, False)

    agent_task = await _create_agent_task(owner, text) if _should_create_agent_task(text, action) else None
    if agent_task:
        await _update_agent_task(agent_task.id, "working", "Generating answer", "Chief started response generation")

    async with AsyncSessionLocal() as session:
        history = list(
            reversed(
                (
                    await session.execute(
                        select(ChatMessage)
                        .where(ChatMessage.user_id == owner)
                        .order_by(ChatMessage.created_at.desc())
                        .limit(settings.max_history_messages)
                    )
                )
                .scalars()
                .all()
            )
        )
        profile = await session.get(UserProfile, owner)
        plans = (
            await session.execute(
                select(DayPlanItem)
                .where(
                    DayPlanItem.user_id == owner,
                    DayPlanItem.plan_date == date.today(),
                    DayPlanItem.status == "active",
                )
                .limit(10)
            )
        ).scalars().all()
        reminders = (
            await session.execute(
                select(Reminder)
                .where(Reminder.user_id == owner, Reminder.status == "active")
                .order_by(Reminder.remind_at.asc())
                .limit(5)
            )
        ).scalars().all()
        memories = (
            await session.execute(
                select(MemoryItem)
                .where(MemoryItem.user_id == owner)
                .order_by(MemoryItem.importance.desc(), MemoryItem.created_at.desc())
                .limit(10)
            )
        ).scalars().all()

    tasks = await task_service.list_open(user_id=owner, task_type="user_task", limit=10)
    agent_tasks = await task_service.list_open(user_id=owner, task_type="agent_task", limit=5)

    system_prompt = _base_system_prompt()
    if profile and profile.profile_text:
        system_prompt += f"\n\nПрофиль пользователя: {profile.profile_text}"
    if memories:
        system_prompt += "\n\nДолгосрочная память:\n" + "\n".join([f"- {m.content}" for m in memories])

    context = []
    if tasks:
        context += ["Активные задачи:"] + [f"- {t.title}" for t in tasks]
    if agent_tasks:
        context += ["Активная работа агентов:"] + [f"- {t.assigned_to}: {t.title} ({t.status})" for t in agent_tasks]
    if plans:
        context += ["План на сегодня:"] + [f"- {p.title}" for p in plans]
    if reminders:
        context += ["Активные напоминания:"] + [f"- {r.text}" for r in reminders]
    if context:
        system_prompt += "\n\nРабочий контекст пользователя:\n" + "\n".join(context)
    system_prompt += (
        "\n\nПравило задач: SQLite tasks table является единственным источником правды. "
        "Не называй выполненные/done/completed задачи активными. "
        "Историю чата и память не используй как источник актуального статуса задач."
    )

    logger.info("web_chat_system_prompt_prepared", owner=owner, prompt_chars=len(system_prompt))
    messages = [{"role": m.role, "content": m.content} for m in history if m.role in {"user", "assistant"}]
    try:
        reply = await ClaudeService().generate_response(
            system_prompt=system_prompt,
            history_messages=messages,
            max_tokens=settings.claude_max_tokens,
        )
        if agent_task:
            await _update_agent_task(agent_task.id, "done", "Completed", "Chief delivered answer")
    except Exception:
        if agent_task:
            await _update_agent_task(agent_task.id, "failed", "Failed", "Claude response failed")
        raise

    await _save_chat_message(owner, "assistant", reply)
    return {"reply": reply, "agent_task_id": agent_task.id if agent_task else None}
