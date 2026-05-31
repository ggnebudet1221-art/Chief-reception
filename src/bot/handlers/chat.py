from __future__ import annotations

import html
import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import delete, select
from sqlalchemy.exc import SQLAlchemyError

from src.core.config import get_settings
from src.infrastructure.db.models.memory import ChatMessage, DayPlanItem, Reminder, Task, UserProfile
from src.infrastructure.db.session import AsyncSessionLocal
from src.infrastructure.logging.logger import get_logger
from src.services.ai.claude_service import (
    ClaudeConfigurationError,
    ClaudeEmptyResponseError,
    ClaudeService,
    ClaudeTemporaryError,
)

router = Router()
logger = get_logger(__name__)

_waiting_profile_users: set[int] = set()
_waiting_task_users: set[int] = set()
_waiting_plan_users: set[int] = set()
_waiting_reminder_users: set[int] = set()
_scheduler = None


def set_reminder_scheduler(scheduler) -> None:
    global _scheduler
    _scheduler = scheduler


def _clear_pending_state(user_id: int) -> None:
    _waiting_task_users.discard(user_id)
    _waiting_plan_users.discard(user_id)
    _waiting_profile_users.discard(user_id)
    _waiting_reminder_users.discard(user_id)


def _resolve_user_id(message: Message, fallback_user_id: int | None = None) -> int:
    if message.from_user:
        return message.from_user.id
    if fallback_user_id is not None:
        return fallback_user_id
    return get_settings().owner_telegram_id or 0


def _resolve_chat_id(message: Message, fallback_chat_id: int | None = None, user_id: int | None = None) -> int:
    if message.chat:
        return message.chat.id
    if fallback_chat_id is not None:
        return fallback_chat_id
    if user_id is not None:
        return user_id
    return get_settings().owner_telegram_id or 0


async def _is_allowed_user(message: Message) -> bool:
    owner_id = get_settings().owner_telegram_id
    uid = message.from_user.id if message.from_user else None
    if owner_id is None:
        return True
    if uid != owner_id:
        await message.answer("Доступ закрыт.")
        return False
    return True


async def _is_allowed_callback(callback: CallbackQuery) -> bool:
    owner_id = get_settings().owner_telegram_id
    uid = callback.from_user.id if callback.from_user else None
    if owner_id is None:
        return True
    if uid != owner_id:
        await callback.answer("Доступ закрыт.", show_alert=True)
        return False
    return True


def _menu_main() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Задачи", callback_data="menu:section:tasks")],
            [InlineKeyboardButton(text="📅 План дня", callback_data="menu:section:plan")],
            [InlineKeyboardButton(text="⏰ Напоминания", callback_data="menu:section:reminders")],
            [InlineKeyboardButton(text="👤 Профиль", callback_data="menu:section:profile")],
            [InlineKeyboardButton(text="🧹 Память", callback_data="menu:section:memory")],
        ]
    )


def _menu_section(section: str) -> InlineKeyboardMarkup:
    sections: dict[str, list[list[str]]] = {
        "tasks": [["➕ Добавить задачу", "menu:tasks:add"], ["📋 Активные задачи", "menu:tasks:list"], ["✅ Выполнить задачу", "menu:tasks:done"], ["🧹 Очистить выполненные", "menu:tasks:clear"], ["⬅️ Назад", "menu:back"]],
        "plan": [["➕ Добавить пункт", "menu:plan:add"], ["📋 План на сегодня", "menu:plan:list"], ["✅ Выполнить пункт", "menu:plan:done"], ["🧹 Очистить выполненные", "menu:plan:clear"], ["⬅️ Назад", "menu:back"]],
        "reminders": [["➕ Добавить напоминание", "menu:reminders:add"], ["📋 Активные напоминания", "menu:reminders:list"], ["❌ Отменить напоминание", "menu:reminders:cancel"], ["⬅️ Назад", "menu:back"]],
        "profile": [["👤 Показать профиль", "menu:profile:show"], ["✏️ Изменить профиль", "menu:profile:set"], ["🧹 Очистить профиль", "menu:profile:clear"], ["⬅️ Назад", "menu:back"]],
        "memory": [["🧹 Очистить историю диалога", "menu:memory:clear"], ["⬅️ Назад", "menu:back"]],
    }
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t, callback_data=c)] for t, c in sections[section]])


def _format_html(text: str) -> str:
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text.strip())
    text = re.sub(r"^[-*]\s+", "• ", text, flags=re.MULTILINE)
    tmp = text.replace("<b>", "@@B@@").replace("</b>", "@@/B@@")
    return html.escape(tmp).replace("@@B@@", "<b>").replace("@@/B@@", "</b>")


def _tz() -> ZoneInfo:
    return ZoneInfo(get_settings().timezone)


def _parse_reminder_input(raw: str) -> tuple[str, datetime] | None:
    now = datetime.now(_tz())
    m = re.search(r"(.+)\|\s*(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})$", raw)
    if m:
        return m.group(1).strip(), datetime.strptime(m.group(2), "%Y-%m-%d %H:%M").replace(tzinfo=_tz())
    m = re.search(r"через\s+(\d+)\s+мин", raw, re.IGNORECASE)
    if m:
        return re.sub(r".*через\s+\d+\s+мин\w*", "", raw, flags=re.IGNORECASE).strip(" ,:-"), now + timedelta(minutes=int(m.group(1)))
    m = re.search(r"через\s+(\d+)\s+час", raw, re.IGNORECASE)
    if m:
        return re.sub(r".*через\s+\d+\s+час\w*", "", raw, flags=re.IGNORECASE).strip(" ,:-"), now + timedelta(hours=int(m.group(1)))
    m = re.search(r"завтра\s+в\s+(\d{1,2}:\d{2})", raw, re.IGNORECASE)
    if m:
        h, mi = map(int, m.group(1).split(":"))
        return re.sub(r".*завтра\s+в\s+\d{1,2}:\d{2}", "", raw, flags=re.IGNORECASE).strip(" ,:-"), (now + timedelta(days=1)).replace(hour=h, minute=mi, second=0, microsecond=0)
    m = re.search(r"на\s+(\d{1,2}:\d{2})", raw, re.IGNORECASE)
    if m:
        h, mi = map(int, m.group(1).split(":"))
        txt = re.sub(r".*на\s+\d{1,2}:\d{2}", "", raw, flags=re.IGNORECASE).strip(" ,:-")
        dt = now.replace(hour=h, minute=mi, second=0, microsecond=0)
        if dt <= now:
            dt += timedelta(days=1)
        return txt, dt
    return None


async def _create_reminder(user_id: int, chat_id: int, text: str, remind_at: datetime) -> Reminder | None:
    if remind_at <= datetime.now(_tz()):
        return None
    async with AsyncSessionLocal() as session:
        rem = Reminder(user_id=user_id, chat_id=chat_id, text=text, remind_at=remind_at, status="active")
        session.add(rem)
        await session.commit()
        await session.refresh(rem)
    if _scheduler is not None:
        _scheduler.schedule_reminder(rem.id, rem.remind_at)
    logger.info("Reminder created", reminder_id=rem.id, user_id=user_id)
    return rem


@router.message(Command("cancel"))
async def cancel_state_handler(message: Message) -> None:
    if not await _is_allowed_user(message):
        return
    uid = message.from_user.id if message.from_user else 0
    _clear_pending_state(uid)
    await message.answer("Текущий режим ожидания отменён.")




@router.message(Command("debugids"))
async def debug_ids_handler(message: Message) -> None:
    if not await _is_allowed_user(message):
        return
    owner = get_settings().owner_telegram_id
    uid = message.from_user.id if message.from_user else None
    cid = message.chat.id if message.chat else None
    await message.answer(f"user_id={uid}\nchat_id={cid}\nOWNER_TELEGRAM_ID={owner}")


@router.message(Command("debugdata"))
async def debug_data_handler(message: Message) -> None:
    if not await _is_allowed_user(message):
        return
    uid = message.from_user.id if message.from_user else 0
    async with AsyncSessionLocal() as session:
        tasks_count = len((await session.execute(select(Task).where(Task.user_id == uid, Task.status == "active"))).scalars().all())
        plan_count = len((await session.execute(select(DayPlanItem).where(DayPlanItem.user_id == uid, DayPlanItem.plan_date == date.today(), DayPlanItem.status == "active"))).scalars().all())
        rem_count = len((await session.execute(select(Reminder).where(Reminder.user_id == uid, Reminder.status == "active"))).scalars().all())
        profile = await session.get(UserProfile, uid)
    await message.answer(f"active_tasks={tasks_count}\nactive_plan_today={plan_count}\nactive_reminders={rem_count}\nprofile_exists={'yes' if profile and profile.profile_text else 'no'}")

@router.message(Command("menu"))
async def menu_handler(message: Message) -> None:
    if not await _is_allowed_user(message):
        return
    await message.answer("Главное меню:", reply_markup=_menu_main())


@router.callback_query(F.data.startswith("menu:"))
async def menu_callback(callback: CallbackQuery) -> None:
    logger.info("Callback clicked", data=callback.data, user_id=callback.from_user.id if callback.from_user else None)
    if not await _is_allowed_callback(callback):
        return
    data = callback.data or ""
    uid = callback.from_user.id if callback.from_user else 0
    try:
        if data.startswith("menu:section:"):
            await callback.message.edit_text("Выбери действие:", reply_markup=_menu_section(data.split(":")[-1]))
        elif data == "menu:back":
            await callback.message.edit_text("Главное меню:", reply_markup=_menu_main())
        elif data == "menu:tasks:add":
            _clear_pending_state(uid)
            _waiting_task_users.add(uid)
            await callback.message.answer("Отправь текст задачи одним сообщением.")
        elif data == "menu:plan:add":
            _clear_pending_state(uid)
            _waiting_plan_users.add(uid)
            await callback.message.answer("Отправь текст пункта плана одним сообщением.")
        elif data == "menu:reminders:add":
            _clear_pending_state(uid)
            _waiting_reminder_users.add(uid)
            await callback.message.answer("Напиши напоминание: текст | YYYY-MM-DD HH:MM")
        elif data == "menu:profile:set":
            _clear_pending_state(uid)
            _waiting_profile_users.add(uid)
            await callback.message.answer("Отправь новый профиль одним сообщением (до 300 символов).")
        elif data == "menu:tasks:list":
            await todo_handler(callback.message, skip_access_check=True, forced_user_id=uid)
        elif data == "menu:plan:list":
            await today_handler(callback.message, skip_access_check=True, forced_user_id=uid)
        elif data == "menu:reminders:list":
            await reminders_handler(callback.message, skip_access_check=True, forced_user_id=uid)
        elif data == "menu:profile:show":
            await profile_handler(callback.message, skip_access_check=True, forced_user_id=uid)
        elif data == "menu:memory:clear":
            async with AsyncSessionLocal() as session:
                result = await session.execute(delete(ChatMessage).where(ChatMessage.user_id == uid))
                await session.commit()
            await callback.message.answer(f"Память очищена. Удалено сообщений: {result.rowcount or 0}.")
        elif data == "menu:tasks:clear":
            await clear_tasks_handler(callback.message, skip_access_check=True, forced_user_id=uid)
        elif data == "menu:plan:clear":
            await clear_plan_handler(callback.message, skip_access_check=True, forced_user_id=uid)
        elif data == "menu:profile:clear":
            await clear_profile_handler(callback.message, skip_access_check=True, forced_user_id=uid)
        elif data == "menu:tasks:done":
            async with AsyncSessionLocal() as session:
                rows = (await session.execute(select(Task).where(Task.user_id == uid, Task.status == "active").order_by(Task.id.asc()))).scalars().all()
            if not rows:
                await callback.message.answer("Активных задач нет.")
            else:
                kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f"✅ Выполнить #{t.id}", callback_data=f"task:done:{t.id}")] for t in rows])
                await callback.message.answer("Выбери задачу:", reply_markup=kb)
        elif data == "menu:plan:done":
            async with AsyncSessionLocal() as session:
                rows = (await session.execute(select(DayPlanItem).where(DayPlanItem.user_id == uid, DayPlanItem.plan_date == date.today(), DayPlanItem.status == "active").order_by(DayPlanItem.id.asc()))).scalars().all()
            if not rows:
                await callback.message.answer("Активных пунктов плана нет.")
            else:
                kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f"✅ Выполнить #{p.id}", callback_data=f"plan:done:{p.id}")] for p in rows])
                await callback.message.answer("Выбери пункт плана:", reply_markup=kb)
        elif data == "menu:reminders:cancel":
            async with AsyncSessionLocal() as session:
                rows = (await session.execute(select(Reminder).where(Reminder.user_id == uid, Reminder.status == "active").order_by(Reminder.remind_at.asc()))).scalars().all()
            if not rows:
                await callback.message.answer("Активных напоминаний нет.")
            else:
                kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f"❌ Отменить #{r.id}", callback_data=f"reminder:cancel:{r.id}")] for r in rows])
                await callback.message.answer("Выбери напоминание:", reply_markup=kb)
    except SQLAlchemyError:
        await callback.message.answer("База временно недоступна.")
    finally:
        await callback.answer()


@router.callback_query(F.data.startswith("task:done:"))
async def task_done_callback(callback: CallbackQuery) -> None:
    if not await _is_allowed_callback(callback):
        return
    uid = callback.from_user.id if callback.from_user else 0
    tid = int((callback.data or "0").split(":")[-1])
    async with AsyncSessionLocal() as session:
        task = await session.get(Task, tid)
        if task is None or task.user_id != uid:
            await callback.answer("Задача не найдена.", show_alert=True)
            return
        task.status = "done"
        task.completed_at = datetime.now(timezone.utc)
        await session.commit()
    await callback.message.answer("Готово. Задача отмечена выполненной.")
    await callback.answer()


@router.callback_query(F.data.startswith("plan:done:"))
async def plan_done_callback(callback: CallbackQuery) -> None:
    if not await _is_allowed_callback(callback):
        return
    uid = callback.from_user.id if callback.from_user else 0
    pid = int((callback.data or "0").split(":")[-1])
    async with AsyncSessionLocal() as session:
        item = await session.get(DayPlanItem, pid)
        if item is None or item.user_id != uid or item.plan_date != date.today():
            await callback.answer("Пункт не найден.", show_alert=True)
            return
        item.status = "done"
        item.completed_at = datetime.now(timezone.utc)
        await session.commit()
    await callback.message.answer("Готово. Пункт плана отмечен выполненным.")
    await callback.answer()


@router.callback_query(F.data.startswith("reminder:cancel:"))
async def reminder_cancel_callback(callback: CallbackQuery) -> None:
    if not await _is_allowed_callback(callback):
        return
    uid = callback.from_user.id if callback.from_user else 0
    rid = int((callback.data or "0").split(":")[-1])
    async with AsyncSessionLocal() as session:
        rem = await session.get(Reminder, rid)
        if rem is None or rem.user_id != uid or rem.status != "active":
            await callback.answer("Напоминание не найдено.", show_alert=True)
            return
        rem.status = "cancelled"
        rem.completed_at = datetime.now(timezone.utc)
        await session.commit()
    await callback.message.answer("Напоминание отменено.")
    await callback.answer()


@router.message(Command("remind"))
async def remind_handler(message: Message) -> None:
    if not await _is_allowed_user(message):
        return
    uid = message.from_user.id if message.from_user else 0
    raw = (message.text or "").replace("/remind", "", 1).strip()
    parsed = _parse_reminder_input(raw)
    if not parsed:
        await message.answer("Не понял время. Пример: /remind проверить монтаж | 2026-05-17 21:30")
        return
    txt, dt = parsed
    if not txt:
        await message.answer("Добавь текст напоминания.")
        return
    try:
        reminder = await _create_reminder(uid, _resolve_chat_id(message, user_id=uid), txt, dt)
    except SQLAlchemyError:
        await message.answer("База временно недоступна.")
        return
    if reminder is None:
        await message.answer("Дата уже в прошлом.")
        return
    _clear_pending_state(uid)
    await message.answer(f"⏰ Напоминание создано: {html.escape(txt)} — {dt.strftime('%Y-%m-%d %H:%M')}")


@router.message(Command("reminders"))
async def reminders_handler(message: Message, skip_access_check: bool = False, forced_user_id: int | None = None) -> None:
    if not skip_access_check and not await _is_allowed_user(message):
        return
    uid = _resolve_user_id(message, forced_user_id)
    try:
        async with AsyncSessionLocal() as session:
            rows = (await session.execute(select(Reminder).where(Reminder.user_id == uid, Reminder.status == "active").order_by(Reminder.remind_at.asc()))).scalars().all()
    except SQLAlchemyError:
        await message.answer("База временно недоступна.")
        return
    if not rows:
        await message.answer("Активных напоминаний нет.")
        return
    lines = ["<b>Активные напоминания</b>"] + [f"• #{r.id} — {html.escape(r.text)} ({r.remind_at.strftime('%Y-%m-%d %H:%M')})" for r in rows]
    await message.answer("\n".join(lines))


@router.message(Command("cancelreminder"))
async def cancel_reminder_handler(message: Message) -> None:
    if not await _is_allowed_user(message):
        return
    uid = message.from_user.id if message.from_user else 0
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer("Коротко: /cancelreminder id")
        return
    rid = int(parts[1])
    async with AsyncSessionLocal() as session:
        rem = await session.get(Reminder, rid)
        if rem is None or rem.user_id != uid or rem.status != "active":
            await message.answer("Напоминание не найдено.")
            return
        rem.status = "cancelled"
        rem.completed_at = datetime.now(timezone.utc)
        await session.commit()
    _clear_pending_state(uid)
    await message.answer("Напоминание отменено.")


@router.message(Command("todo"))
async def todo_handler(message: Message, skip_access_check: bool = False, forced_user_id: int | None = None) -> None:
    if not skip_access_check and not await _is_allowed_user(message):
        return
    uid = _resolve_user_id(message, forced_user_id)
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(select(Task).where(Task.user_id == uid, Task.status == "active").order_by(Task.id.asc()))).scalars().all()
    if not rows:
        await message.answer("Активных задач нет.")
        return
    await message.answer("\n".join(["<b>Активные задачи</b>"] + [f"• #{t.id} — {html.escape(t.title)}" for t in rows]))


@router.message(Command("today"))
async def today_handler(message: Message, skip_access_check: bool = False, forced_user_id: int | None = None) -> None:
    if not skip_access_check and not await _is_allowed_user(message):
        return
    uid = _resolve_user_id(message, forced_user_id)
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(select(DayPlanItem).where(DayPlanItem.user_id == uid, DayPlanItem.plan_date == date.today()).order_by(DayPlanItem.id.asc()))).scalars().all()
    if not rows:
        await message.answer("На сегодня пунктов плана нет.")
        return
    active = [r for r in rows if r.status == "active"]
    done = [r for r in rows if r.status == "done"]
    lines = ["<b>План на сегодня</b>"]
    if active:
        lines += ["\n<b>Активные:</b>"] + [f"• #{r.id} — {html.escape(r.title)}" for r in active]
    if done:
        lines += ["\n<b>Выполненные:</b>"] + [f"• #{r.id} — {html.escape(r.title)}" for r in done]
    await message.answer("\n".join(lines))


@router.message(Command("profile"))
async def profile_handler(message: Message, skip_access_check: bool = False, forced_user_id: int | None = None) -> None:
    if not skip_access_check and not await _is_allowed_user(message):
        return
    uid = _resolve_user_id(message, forced_user_id)
    async with AsyncSessionLocal() as session:
        profile = await session.get(UserProfile, uid)
    text = profile.profile_text if profile and profile.profile_text else "(профиль не задан)"
    await message.answer(f"Текущий профиль:\n{html.escape(text)}")


@router.message(Command("clearprofile"))
async def clear_profile_handler(message: Message, skip_access_check: bool = False, forced_user_id: int | None = None) -> None:
    if not skip_access_check and not await _is_allowed_user(message):
        return
    uid = _resolve_user_id(message, forced_user_id)
    async with AsyncSessionLocal() as session:
        profile = await session.get(UserProfile, uid)
        if profile:
            await session.delete(profile)
            await session.commit()
    _clear_pending_state(uid)
    await message.answer("Профиль очищен.")


@router.message(Command("cleartasks"))
async def clear_tasks_handler(message: Message, skip_access_check: bool = False, forced_user_id: int | None = None) -> None:
    if not skip_access_check and not await _is_allowed_user(message):
        return
    uid = _resolve_user_id(message, forced_user_id)
    async with AsyncSessionLocal() as session:
        res = await session.execute(delete(Task).where(Task.user_id == uid, Task.status == "done"))
        await session.commit()
    await message.answer(f"Удалено выполненных задач: {res.rowcount or 0}.")


@router.message(Command("clearplan"))
async def clear_plan_handler(message: Message, skip_access_check: bool = False, forced_user_id: int | None = None) -> None:
    if not skip_access_check and not await _is_allowed_user(message):
        return
    uid = _resolve_user_id(message, forced_user_id)
    async with AsyncSessionLocal() as session:
        res = await session.execute(delete(DayPlanItem).where(DayPlanItem.user_id == uid, DayPlanItem.plan_date == date.today(), DayPlanItem.status == "done"))
        await session.commit()
    await message.answer(f"Удалено выполненных пунктов плана: {res.rowcount or 0}.")


@router.message()
async def chat_handler(message: Message) -> None:
    logger.info("Message handler called")
    if not await _is_allowed_user(message):
        return
    uid = message.from_user.id if message.from_user else 0
    text = (message.text or "").strip()
    if not text:
        await message.answer("Пожалуйста, отправь текстовое сообщение.")
        return

    if uid in _waiting_task_users:
        async with AsyncSessionLocal() as session:
            session.add(Task(user_id=uid, title=text, status="active"))
            await session.commit()
        _clear_pending_state(uid)
        await message.answer("Задача добавлена.")
        return

    if uid in _waiting_plan_users:
        async with AsyncSessionLocal() as session:
            session.add(DayPlanItem(user_id=uid, title=text, status="active", plan_date=date.today()))
            await session.commit()
        _clear_pending_state(uid)
        await message.answer("Пункт плана добавлен.")
        return

    if uid in _waiting_profile_users:
        if len(text) > 300:
            await message.answer("Профиль слишком длинный. Сократи до 300 символов.")
            return
        async with AsyncSessionLocal() as session:
            profile = await session.get(UserProfile, uid)
            if profile is None:
                session.add(UserProfile(user_id=uid, profile_text=text))
            else:
                profile.profile_text = text
            await session.commit()
        _clear_pending_state(uid)
        await message.answer("Профиль сохранён.")
        return

    if uid in _waiting_reminder_users:
        parsed = _parse_reminder_input(text)
        if not parsed:
            await message.answer("Не понял время. Напиши так: напомни через 20 минут проверить монтаж или /remind текст | YYYY-MM-DD HH:MM")
            return
        t, dt = parsed
        rem = await _create_reminder(uid, _resolve_chat_id(message, user_id=uid), t, dt)
        if rem is None:
            await message.answer("Дата уже в прошлом.")
            return
        _clear_pending_state(uid)
        await message.answer(f"⏰ Напоминание создано: {html.escape(t)} — {dt.strftime('%Y-%m-%d %H:%M')}")
        return

    if re.search(r"напомни|создай напоминание|поставь напоминание|напомнить", text, re.IGNORECASE):
        parsed = _parse_reminder_input(text)
        if not parsed:
            await message.answer("Напиши так: напомни через 20 минут проверить монтаж\nили /remind проверить монтаж | 2026-05-17 21:30")
            return
        t, dt = parsed
        rem = await _create_reminder(uid, _resolve_chat_id(message, user_id=uid), t, dt)
        if rem is None:
            await message.answer("Дата уже в прошлом.")
            return
        _clear_pending_state(uid)
        await message.answer(f"⏰ Напоминание создано: {html.escape(t)} — {dt.strftime('%Y-%m-%d %H:%M')}")
        return

    settings = get_settings()
    async with AsyncSessionLocal() as session:
        session.add(ChatMessage(user_id=uid, role="user", content=text))
        await session.commit()
        history = list(reversed((await session.execute(select(ChatMessage).where(ChatMessage.user_id == uid).order_by(ChatMessage.created_at.desc()).limit(settings.max_history_messages))).scalars().all()))
        profile = await session.get(UserProfile, uid)
        tasks = (await session.execute(select(Task).where(Task.user_id == uid, Task.status == "active").order_by(Task.id.asc()).limit(10))).scalars().all()
        plans = (await session.execute(select(DayPlanItem).where(DayPlanItem.user_id == uid, DayPlanItem.plan_date == date.today(), DayPlanItem.status == "active").order_by(DayPlanItem.id.asc()).limit(10))).scalars().all()
        reminders = (await session.execute(select(Reminder).where(Reminder.user_id == uid, Reminder.status == "active").order_by(Reminder.remind_at.asc()).limit(5))).scalars().all()

    system_prompt = settings.default_system_prompt
    if profile and profile.profile_text:
        system_prompt += f"\n\nИнформация о пользователе: {profile.profile_text}"
    ctx: list[str] = []
    if tasks:
        ctx += ["Активные задачи:"] + [f"{i+1}. {t.title}" for i, t in enumerate(tasks)]
    if plans:
        if ctx:
            ctx.append("")
        ctx += ["План на сегодня:"] + [f"{i+1}. {p.title}" for i, p in enumerate(plans)]
    if reminders:
        if ctx:
            ctx.append("")
        ctx += ["Ближайшие напоминания:"] + [f"{i+1}. {r.text} ({r.remind_at.strftime('%H:%M')})" for i, r in enumerate(reminders)]
    if ctx:
        system_prompt += "\n\nРабочий контекст пользователя:\n" + "\n".join(ctx)

    msgs = [{"role": m.role, "content": m.content} for m in history if m.role in {"user", "assistant"}]
    try:
        reply = await ClaudeService().generate_response(system_prompt=system_prompt, history_messages=msgs, max_tokens=settings.claude_max_tokens)
    except (ClaudeConfigurationError, ClaudeTemporaryError, ClaudeEmptyResponseError):
        await message.answer("Сервис AI временно недоступен. Попробуй чуть позже.")
        return

    async with AsyncSessionLocal() as session:
        session.add(ChatMessage(user_id=uid, role="assistant", content=reply))
        await session.commit()
    await message.answer(_format_html(reply))

