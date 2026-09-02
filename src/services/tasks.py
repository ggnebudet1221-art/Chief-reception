from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from src.infrastructure.db.models.memory import Task
from src.infrastructure.db.session import AsyncSessionLocal

ACTIVE_STATUSES = {"active", "delegated", "in_progress"}
COMPLETED_STATUSES = {"completed"}
ARCHIVED_STATUSES = {"archived"}
OPEN_STATUSES = ACTIVE_STATUSES

STATUS_ALIASES = {
    "pending": "active",
    "thinking": "in_progress",
    "working": "in_progress",
    "waiting": "in_progress",
    "done": "completed",
    "failed": "completed",
}


def normalize_status(status: str | None) -> str:
    value = (status or "active").strip().lower()
    value = STATUS_ALIASES.get(value, value)
    if value not in {"active", "delegated", "in_progress", "completed", "archived"}:
        return "active"
    return value


@dataclass(frozen=True)
class TaskCreate:
    user_id: int
    title: str
    description: str = ""
    task_type: str = "user_task"
    status: str = "active"
    assigned_agent: str = "Chief"
    created_by: str = "user"
    source: str = "manual"
    context: str = ""
    current_step: str = "Captured"
    action_log: str = ""


class TaskService:
    async def create(self, payload: TaskCreate) -> Task:
        assigned_agent = (payload.assigned_agent or "Chief").strip()
        async with AsyncSessionLocal() as session:
            task = Task(
                user_id=payload.user_id,
                title=payload.title.strip(),
                description=payload.description.strip(),
                status=normalize_status(payload.status),
                task_type=payload.task_type if payload.task_type in {"user_task", "agent_task", "system_task"} else "user_task",
                assigned_to=assigned_agent,
                assigned_agent=assigned_agent,
                created_by=payload.created_by.strip() or "user",
                source=payload.source.strip() or "manual",
                context=payload.context.strip(),
                current_step=payload.current_step.strip(),
                action_log=payload.action_log.strip(),
            )
            session.add(task)
            await session.commit()
            await session.refresh(task)
            return task

    async def find_open_user_task_by_title(self, user_id: int, title: str) -> Task | None:
        normalized_title = title.strip().lower()
        if not normalized_title:
            return None
        rows = await self.list_open(user_id=user_id, task_type="user_task", limit=100)
        return next((task for task in rows if task.title.strip().lower() == normalized_title), None)

    async def list_open(self, user_id: int, task_type: str | None = None, limit: int = 80) -> list[Task]:
        async with AsyncSessionLocal() as session:
            query = select(Task).where(Task.user_id == user_id, Task.status.in_(OPEN_STATUSES))
            if task_type:
                query = query.where(Task.task_type == task_type)
            rows = (await session.execute(query.order_by(Task.created_at.asc(), Task.id.asc()).limit(limit))).scalars().all()
        return list(rows)

    async def list_completed(self, user_id: int, task_type: str | None = None, limit: int = 80) -> list[Task]:
        async with AsyncSessionLocal() as session:
            query = select(Task).where(Task.user_id == user_id, Task.status.in_(COMPLETED_STATUSES))
            if task_type:
                query = query.where(Task.task_type == task_type)
            rows = (
                await session.execute(
                    query.order_by(Task.completed_at.desc(), Task.updated_at.desc(), Task.id.desc()).limit(limit)
                )
            ).scalars().all()
        return list(rows)

    async def queue(self, user_id: int, limit: int = 120) -> dict:
        async with AsyncSessionLocal() as session:
            rows = (
                await session.execute(
                    select(Task).where(Task.user_id == user_id).order_by(Task.updated_at.desc(), Task.id.desc()).limit(limit)
                )
            ).scalars().all()

        active = [task for task in rows if task.status in ACTIVE_STATUSES]
        delegated = [task for task in active if task.status == "delegated"]
        in_progress = [task for task in active if task.status == "in_progress"]
        completed = [task for task in rows if task.status in COMPLETED_STATUSES]
        archived = [task for task in rows if task.status in ARCHIVED_STATUSES]
        active_user = [task for task in active if task.task_type == "user_task"]
        active_agent = [task for task in active if task.task_type == "agent_task"]
        active_system = [task for task in active if task.task_type == "system_task"]
        return {
            "active": [task_to_dict(task) for task in active],
            "delegated": [task_to_dict(task) for task in delegated],
            "in_progress": [task_to_dict(task) for task in in_progress],
            "completed": [task_to_dict(task) for task in completed],
            "archived": [task_to_dict(task) for task in archived],
            "active_user": [task_to_dict(task) for task in active_user],
            "active_agent": [task_to_dict(task) for task in active_agent],
            "active_system": [task_to_dict(task) for task in active_system],
            "counts": {
                "active": len(active),
                "delegated": len(delegated),
                "in_progress": len(in_progress),
                "active_user": len(active_user),
                "active_agent": len(active_agent),
                "active_system": len(active_system),
                "completed": len(completed),
                "archived": len(archived),
                "total": len(rows),
            },
        }

    async def get_for_user(self, task_id: int, user_id: int) -> Task | None:
        async with AsyncSessionLocal() as session:
            task = await session.get(Task, task_id)
            if task is None or task.user_id != user_id:
                return None
            return task

    async def set_status(
        self,
        task_id: int,
        status: str,
        step: str = "",
        log_line: str = "",
        result: str = "",
    ) -> None:
        async with AsyncSessionLocal() as session:
            task = await session.get(Task, task_id)
            if task is None:
                return
            task.status = normalize_status(status)
            task.updated_at = datetime.now(timezone.utc)
            if step:
                task.current_step = step
            if log_line:
                task.action_log = ((task.action_log or "") + f"\n{log_line}").strip()
            if result:
                task.result = result
            if task.status in COMPLETED_STATUSES:
                task.completed_at = datetime.now(timezone.utc)
            await session.commit()

    async def expire_stale_agent_tasks(self, user_id: int, older_than_seconds: int = 900) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=older_than_seconds)
        async with AsyncSessionLocal() as session:
            rows = (
                await session.execute(
                    select(Task).where(
                        Task.user_id == user_id,
                        Task.task_type == "agent_task",
                        Task.status.in_(OPEN_STATUSES),
                        Task.updated_at < cutoff,
                    )
                )
            ).scalars().all()
            for task in rows:
                task.status = "completed"
                task.current_step = "Expired"
                task.result = task.result or "Expired by stale task cleanup"
                task.completed_at = datetime.now(timezone.utc)
                task.updated_at = datetime.now(timezone.utc)
                task.action_log = ((task.action_log or "") + "\nExpired by stale task cleanup").strip()
            await session.commit()
            return len(rows)

    async def complete(self, task_id: int, user_id: int, result: str = "") -> None:
        async with AsyncSessionLocal() as session:
            task = await session.get(Task, task_id)
            if task is None or task.user_id != user_id:
                return
            task.status = "completed"
            task.result = result or task.result or ""
            task.current_step = "Completed"
            task.completed_at = datetime.now(timezone.utc)
            task.updated_at = datetime.now(timezone.utc)
            await session.commit()


def task_to_dict(task: Task) -> dict:
    assigned_agent = task.assigned_agent or task.assigned_to or "Chief"
    return {
        "id": task.id,
        "title": task.title,
        "description": task.description or "",
        "status": normalize_status(task.status),
        "type": task.task_type,
        "task_type": task.task_type,
        "assigned_agent": assigned_agent,
        "assigned_to": assigned_agent,
        "created_by": task.created_by or "",
        "source": task.source or "",
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "updated_at": task.updated_at.isoformat() if task.updated_at else None,
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
        "result": task.result or "",
        "context": task.context or "",
        "action_log": task.action_log or "",
        "current_step": task.current_step or "",
    }
