from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select

from src.api.deps import require_token
from src.core.config import get_settings
from src.infrastructure.db.models.memory import Task
from src.infrastructure.db.session import AsyncSessionLocal

router = APIRouter(prefix="/api/tasks", tags=["web-tasks"], dependencies=[Depends(require_token)])


class TaskIn(BaseModel):
    title: str


@router.get("")
async def list_tasks() -> list[dict]:
    owner = get_settings().web_owner_id
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(select(Task).where(Task.user_id == owner, Task.status == "active").order_by(Task.id.asc()))).scalars().all()
    return [{"id": t.id, "title": t.title, "status": t.status} for t in rows]


@router.post("")
async def add_task(payload: TaskIn) -> dict:
    owner = get_settings().web_owner_id
    async with AsyncSessionLocal() as session:
        task = Task(user_id=owner, title=payload.title.strip(), status="active")
        session.add(task)
        await session.commit()
        await session.refresh(task)
    return {"id": task.id, "title": task.title, "status": task.status}


@router.post("/{task_id}/done")
async def done_task(task_id: int) -> dict:
    owner = get_settings().web_owner_id
    async with AsyncSessionLocal() as session:
        task = await session.get(Task, task_id)
        if task and task.user_id == owner:
            task.status = "done"
            task.completed_at = datetime.now(timezone.utc)
            await session.commit()
    return {"ok": True}
