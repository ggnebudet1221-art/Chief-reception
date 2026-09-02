from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from src.api.deps import require_token
from src.core.config import get_settings
from src.services.tasks import TaskCreate, TaskService, task_to_dict

router = APIRouter(prefix="/api/tasks", tags=["web-tasks"], dependencies=[Depends(require_token)])
task_service = TaskService()


class TaskIn(BaseModel):
    title: str
    type: str = "user_task"
    assigned_to: str = "Chief"
    assigned_agent: str = ""
    source: str = "manual"
    description: str = ""
    context: str = ""


@router.get("")
async def list_tasks() -> list[dict]:
    owner = get_settings().web_owner_id
    rows = await task_service.list_open(user_id=owner, task_type="user_task")
    return [task_to_dict(task) for task in rows]


@router.get("/queue")
async def task_queue() -> dict:
    owner = get_settings().web_owner_id
    return await task_service.queue(user_id=owner)


@router.get("/{task_id}")
async def task_details(task_id: int) -> dict:
    owner = get_settings().web_owner_id
    task = await task_service.get_for_user(task_id=task_id, user_id=owner)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task_to_dict(task)


@router.post("")
async def add_task(payload: TaskIn) -> dict:
    owner = get_settings().web_owner_id
    task = await task_service.create(
        TaskCreate(
            user_id=owner,
            title=payload.title.strip(),
            task_type=payload.type,
            assigned_agent=(payload.assigned_agent or payload.assigned_to).strip() or "Chief",
            created_by="workspace",
            source=payload.source.strip() or "manual",
            description=payload.description.strip(),
            context=payload.context.strip(),
            current_step="Captured",
            action_log="Created from workspace",
        )
    )
    return task_to_dict(task)


@router.post("/{task_id}/done")
async def done_task(task_id: int) -> dict:
    owner = get_settings().web_owner_id
    await task_service.complete(task_id=task_id, user_id=owner)
    return {"ok": True}
