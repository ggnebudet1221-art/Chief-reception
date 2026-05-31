from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select

from src.api.deps import require_token
from src.core.config import get_settings
from src.infrastructure.db.models.memory import Reminder
from src.infrastructure.db.session import AsyncSessionLocal

router = APIRouter(prefix="/api/reminders", tags=["web-reminders"], dependencies=[Depends(require_token)])


class ReminderIn(BaseModel):
    text: str
    remind_at: str


@router.get("")
async def list_reminders() -> list[dict]:
    owner = get_settings().web_owner_id
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(select(Reminder).where(Reminder.user_id == owner, Reminder.status == "active").order_by(Reminder.remind_at.asc()))).scalars().all()
    return [{"id": r.id, "text": r.text, "remind_at": r.remind_at.isoformat()} for r in rows]


@router.post("")
async def add_reminder(payload: ReminderIn) -> dict:
    owner = get_settings().web_owner_id
    dt = datetime.fromisoformat(payload.remind_at)
    async with AsyncSessionLocal() as session:
        r = Reminder(user_id=owner, chat_id=owner, text=payload.text.strip(), remind_at=dt, status="active")
        session.add(r)
        await session.commit()
        await session.refresh(r)
    return {"id": r.id, "text": r.text}


@router.post("/{reminder_id}/cancel")
async def cancel_reminder(reminder_id: int) -> dict:
    owner = get_settings().web_owner_id
    async with AsyncSessionLocal() as session:
        r = await session.get(Reminder, reminder_id)
        if r and r.user_id == owner:
            r.status = "cancelled"
            r.completed_at = datetime.now(timezone.utc)
            await session.commit()
    return {"ok": True}
