from fastapi import APIRouter, Depends
from sqlalchemy import select

from src.api.deps import require_token
from src.core.config import get_settings
from src.infrastructure.db.models.memory import MemoryItem
from src.infrastructure.db.session import AsyncSessionLocal

router = APIRouter(prefix="/api/status", tags=["web-status"], dependencies=[Depends(require_token)])


@router.get("")
async def status() -> dict:
    s = get_settings()
    async with AsyncSessionLocal() as session:
        memories_count = len((await session.execute(select(MemoryItem).where(MemoryItem.user_id == s.web_owner_id))).scalars().all())
    return {
        "model": s.anthropic_model,
        "max_history_messages": s.max_history_messages,
        "max_tokens": s.claude_max_tokens,
        "api": "ok",
        "memories_count": memories_count,
        "version": "v0.3.0",
    }
