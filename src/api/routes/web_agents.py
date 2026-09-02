from fastapi import APIRouter, Depends
from sqlalchemy import select

from src.api.deps import require_token
from src.core.config import get_settings
from src.infrastructure.db.models.memory import AgentMessage, Task
from src.infrastructure.db.session import AsyncSessionLocal
from src.services.agents.registry import AgentRegistry
from src.services.tasks import ACTIVE_STATUSES

router = APIRouter(tags=["web-agents"], dependencies=[Depends(require_token)])


@router.get("/api/agents")
async def list_agents() -> list[dict]:
    owner = get_settings().web_owner_id
    registry = AgentRegistry()
    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                select(Task)
                .where(Task.user_id == owner, Task.task_type == "agent_task", Task.status.in_(ACTIVE_STATUSES))
                .order_by(Task.updated_at.desc(), Task.id.desc())
                .limit(80)
            )
        ).scalars().all()

    agents = []
    for agent in registry.all():
        agent_tasks = [task for task in rows if (task.assigned_agent or task.assigned_to or "").lower() == agent.name.lower()]
        active = next((task for task in agent_tasks if task.status == "in_progress"), None)
        delegated = next((task for task in agent_tasks if task.status == "delegated"), None)
        queued = next((task for task in agent_tasks if task.status == "active"), None)
        current = active or delegated or queued
        if active:
            status = "Working"
        elif delegated:
            status = "Delegated"
        elif queued:
            status = "Queued"
        else:
            status = "Idle"
        agents.append(
            {
                "id": agent.id,
                "name": agent.name,
                "role": agent.role,
                "online": bool(agent.bot_token),
                "status": status,
                "active_tasks": len(agent_tasks),
                "current_task_id": current.id if current else None,
                "current_task": current.title if current else "",
            }
        )
    return agents


@router.get("/api/agents/activity")
async def agent_activity() -> list[dict]:
    async with AsyncSessionLocal() as session:
        messages = (
            await session.execute(
                select(AgentMessage).order_by(AgentMessage.created_at.desc(), AgentMessage.id.desc()).limit(40)
            )
        ).scalars().all()
        task_ids = [message.task_id for message in messages if message.task_id]
        tasks_by_id = {}
        if task_ids:
            tasks = (await session.execute(select(Task).where(Task.id.in_(task_ids)))).scalars().all()
            tasks_by_id = {task.id: task for task in tasks}

    return [
        {
            "id": message.id,
            "task_id": message.task_id,
            "task_title": tasks_by_id.get(message.task_id).title if message.task_id in tasks_by_id else "",
            "from_agent": message.from_agent,
            "to_agent": message.to_agent,
            "channel": message.channel,
            "content": message.content,
            "status": message.status,
            "created_at": message.created_at.isoformat() if message.created_at else None,
        }
        for message in reversed(messages)
    ]
