from datetime import date, datetime, timedelta
import re

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select

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
from src.services.ai.claude_service import ClaudeService

router = APIRouter(tags=["web-chat"], dependencies=[Depends(require_token)])
logger = get_logger(__name__)


class ChatIn(BaseModel):
    message: str


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


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
    t = _norm(msg)
    low = t.lower()

    if low.startswith("запомни"):
        payload = _norm(re.sub(r"^запомни[:,]?", "", t, flags=re.IGNORECASE))
        return "memory", {"content": payload[:300]}

    if any(k in low for k in ["напомни", "создай напоминание", "поставь напоминание"]):
        dt, title = _parse_reminder_time(t)
        return "reminder", {"remind_at": dt, "text": (title or "")[:200]}

    if any(k in low for k in ["добавь задачу", "создай задачу"]):
        title = _norm(re.sub(r".*(добавь задачу|создай задачу)", "", t, flags=re.IGNORECASE))
        return "task", {"title": title[:200]}

    if any(k in low for k in ["запланируй", "добавь в план", "в план на сегодня"]):
        title = _norm(re.sub(r".*(запланируй|добавь в план|в план на сегодня)", "", t, flags=re.IGNORECASE))
        return "plan", {"title": title[:200]}

    return None, {}


def _base_system_prompt() -> str:
    settings = get_settings()
    raw = (settings.default_system_prompt or "").strip()
    fallback = "Ты — личный ИИ-ассистент AI Manager. Всегда отвечай на русском языке, кратко, понятно и полезно."
    prompt = raw or fallback
    prompt += (
        "\n\nТы AI Manager — личный центр управления днём. "
        "Всегда отвечай на русском, если пользователь явно не попросил другой язык. "
        "Никогда не называй себя Kiro."
    )
    return prompt


@router.post("/api/chat")
async def chat(payload: ChatIn) -> dict:
    settings = get_settings()
    owner = settings.web_owner_id
    text = _norm(payload.message)

    action, data = _detect_action(text)
    if action:
        logger.info("chat_action_detected", action=action, owner=owner)
        logger.info("chat_action_payload_parsed", action=action, owner=owner, payload_preview=str(data)[:200])

    if action == "memory":
        if not data["content"]:
            return {"reply": "Не вижу текста для памяти.", "action": "memory", "created": False}
        try:
            async with AsyncSessionLocal() as session:
                memory = MemoryItem(user_id=owner, content=data["content"], category="manual", importance=5)
                session.add(memory)
                await session.commit()
                await session.refresh(memory)
            logger.info("chat_action_db_create_success", action=action, owner=owner, entity_id=memory.id)
            logger.info("chat_action_frontend_refresh_triggered", action=action, owner=owner)
            return {"reply": f"✅ Память сохранена: {memory.content}", "action": "memory", "created": True}
        except Exception:
            logger.exception("chat_action_db_create_failed", action=action, owner=owner)
            return {"reply": "❌ Не удалось сохранить в память. Попробуйте ещё раз.", "action": "memory", "created": False}

    if action == "reminder":
        if data.get("remind_at") is None:
            return {"reply": "Не понял время. Напиши, например: напомни через 30 минут проверить монтаж.", "action": "reminder", "created": False}
        if not data.get("text"):
            return {"reply": "Укажи текст напоминания.", "action": "reminder", "created": False}
        try:
            async with AsyncSessionLocal() as session:
                reminder = Reminder(user_id=owner, chat_id=owner, text=data["text"], remind_at=data["remind_at"], status="active")
                session.add(reminder)
                await session.commit()
                await session.refresh(reminder)
            logger.info("chat_action_db_create_success", action=action, owner=owner, entity_id=reminder.id)
            logger.info("chat_action_frontend_refresh_triggered", action=action, owner=owner)
            return {"reply": f"✅ Напоминание создано: {reminder.text}", "action": "reminder", "created": True}
        except Exception:
            logger.exception("chat_action_db_create_failed", action=action, owner=owner)
            return {"reply": "❌ Не удалось создать напоминание. Попробуйте ещё раз.", "action": "reminder", "created": False}

    if action == "task":
        if not data.get("title"):
            return {"reply": "Укажи название задачи.", "action": "task", "created": False}
        try:
            async with AsyncSessionLocal() as session:
                task = Task(user_id=owner, title=data["title"], status="active")
                session.add(task)
                await session.commit()
                await session.refresh(task)
            logger.info("chat_action_db_create_success", action=action, owner=owner, entity_id=task.id)
            logger.info("chat_action_frontend_refresh_triggered", action=action, owner=owner)
            return {"reply": f"✅ Задача создана: {task.title}", "action": "task", "created": True}
        except Exception:
            logger.exception("chat_action_db_create_failed", action=action, owner=owner)
            return {"reply": "❌ Не удалось создать задачу. Попробуйте ещё раз.", "action": "task", "created": False}

    if action == "plan":
        if not data.get("title"):
            return {"reply": "Укажи пункт плана.", "action": "plan", "created": False}
        try:
            async with AsyncSessionLocal() as session:
                plan_item = DayPlanItem(user_id=owner, title=data["title"], status="active", plan_date=date.today())
                session.add(plan_item)
                await session.commit()
                await session.refresh(plan_item)
            logger.info("chat_action_db_create_success", action=action, owner=owner, entity_id=plan_item.id)
            logger.info("chat_action_frontend_refresh_triggered", action=action, owner=owner)
            return {"reply": f"✅ Пункт плана создан: {plan_item.title}", "action": "plan", "created": True}
        except Exception:
            logger.exception("chat_action_db_create_failed", action=action, owner=owner)
            return {"reply": "❌ Не удалось создать пункт плана. Попробуйте ещё раз.", "action": "plan", "created": False}

    async with AsyncSessionLocal() as session:
        session.add(ChatMessage(user_id=owner, role="user", content=text))
        await session.commit()
        history = list(reversed((await session.execute(select(ChatMessage).where(ChatMessage.user_id == owner).order_by(ChatMessage.created_at.desc()).limit(settings.max_history_messages))).scalars().all()))
        profile = await session.get(UserProfile, owner)
        tasks = (await session.execute(select(Task).where(Task.user_id == owner, Task.status == "active").limit(10))).scalars().all()
        plans = (await session.execute(select(DayPlanItem).where(DayPlanItem.user_id == owner, DayPlanItem.plan_date == date.today(), DayPlanItem.status == "active").limit(10))).scalars().all()
        reminders = (await session.execute(select(Reminder).where(Reminder.user_id == owner, Reminder.status == "active").order_by(Reminder.remind_at.asc()).limit(5))).scalars().all()
        memories = (await session.execute(select(MemoryItem).where(MemoryItem.user_id == owner).order_by(MemoryItem.importance.desc(), MemoryItem.created_at.desc()).limit(10))).scalars().all()

    system_prompt = _base_system_prompt()
    if profile and profile.profile_text:
        system_prompt += f"\n\nПрофиль пользователя: {profile.profile_text}"
    if memories:
        system_prompt += "\n\nДолгосрочная память:\n" + "\n".join([f"- {m.content}" for m in memories])
    context = []
    if tasks:
        context += ["Активные задачи:"] + [f"- {t.title}" for t in tasks]
    if plans:
        context += ["План на сегодня:"] + [f"- {p.title}" for p in plans]
    if reminders:
        context += ["Активные напоминания:"] + [f"- {r.text}" for r in reminders]
    if context:
        system_prompt += "\n\nРабочий контекст пользователя:\n" + "\n".join(context)

    logger.info("Web chat system prompt prepared", owner=owner, preview=system_prompt[:240])
    msgs = [{"role": m.role, "content": m.content} for m in history if m.role in {"user", "assistant"}]
    reply = await ClaudeService().generate_response(system_prompt=system_prompt, history_messages=msgs, max_tokens=settings.claude_max_tokens)

    async with AsyncSessionLocal() as session:
        session.add(ChatMessage(user_id=owner, role="assistant", content=reply))
        await session.commit()

    return {"reply": reply}
