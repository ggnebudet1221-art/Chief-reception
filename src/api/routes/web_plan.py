from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select

from src.api.deps import require_token
from src.core.config import get_settings
from src.infrastructure.db.models.memory import DayPlanItem
from src.infrastructure.db.session import AsyncSessionLocal

router = APIRouter(prefix="/api/plan", tags=["web-plan"], dependencies=[Depends(require_token)])


class PlanIn(BaseModel):
    title: str


@router.get("/today")
async def list_today_plan() -> list[dict]:
    owner = get_settings().web_owner_id
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(select(DayPlanItem).where(DayPlanItem.user_id == owner, DayPlanItem.plan_date == date.today(), DayPlanItem.status == "active").order_by(DayPlanItem.id.asc()))).scalars().all()
    return [{"id": p.id, "title": p.title, "status": p.status} for p in rows]


@router.post("")
async def add_plan(payload: PlanIn) -> dict:
    owner = get_settings().web_owner_id
    async with AsyncSessionLocal() as session:
        item = DayPlanItem(user_id=owner, title=payload.title.strip(), status="active", plan_date=date.today())
        session.add(item)
        await session.commit()
        await session.refresh(item)
    return {"id": item.id, "title": item.title, "status": item.status}


@router.post("/{item_id}/done")
async def done_plan(item_id: int) -> dict:
    owner = get_settings().web_owner_id
    async with AsyncSessionLocal() as session:
        item = await session.get(DayPlanItem, item_id)
        if item and item.user_id == owner:
            item.status = "done"
            item.completed_at = datetime.now(timezone.utc)
            await session.commit()
    return {"ok": True}
