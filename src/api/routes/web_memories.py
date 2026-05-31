from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from src.api.deps import require_token
from src.core.config import get_settings
from src.infrastructure.db.models.memory import MemoryItem
from src.infrastructure.db.session import AsyncSessionLocal

router = APIRouter(prefix="/api/memories", tags=["web-memories"], dependencies=[Depends(require_token)])


class MemoryIn(BaseModel):
    content: str = Field(min_length=1, max_length=300)
    category: str = Field(default="general", max_length=64)
    importance: int = Field(default=3, ge=1, le=5)


@router.get("")
async def list_memories() -> list[dict]:
    owner = get_settings().web_owner_id
    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                select(MemoryItem)
                .where(MemoryItem.user_id == owner)
                .order_by(MemoryItem.importance.desc(), MemoryItem.created_at.desc())
                .limit(100)
            )
        ).scalars().all()
    return [
        {
            "id": m.id,
            "content": m.content,
            "category": m.category,
            "importance": m.importance,
            "created_at": m.created_at.isoformat(),
        }
        for m in rows
    ]


@router.post("")
async def add_memory(payload: MemoryIn) -> dict:
    owner = get_settings().web_owner_id
    async with AsyncSessionLocal() as session:
        m = MemoryItem(
            user_id=owner,
            content=payload.content.strip()[:300],
            category=payload.category.strip()[:64] or "general",
            importance=payload.importance,
        )
        session.add(m)
        await session.commit()
        await session.refresh(m)
    return {"id": m.id}


@router.delete("/{memory_id}")
async def delete_memory(memory_id: int) -> dict:
    owner = get_settings().web_owner_id
    async with AsyncSessionLocal() as session:
        await session.execute(delete(MemoryItem).where(MemoryItem.id == memory_id, MemoryItem.user_id == owner))
        await session.commit()
    return {"ok": True}
