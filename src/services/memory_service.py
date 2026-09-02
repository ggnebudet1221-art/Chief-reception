from __future__ import annotations

from collections import defaultdict

from sqlalchemy import delete, select

from src.infrastructure.db.models.memory import MemoryItem
from src.infrastructure.db.session import AsyncSessionLocal
from src.infrastructure.logging.logger import get_logger

logger = get_logger(__name__)


DEFAULT_USER_MEMORIES: tuple[tuple[str, str, int], ...] = (
    ("profile", "Имя: Артём", 5),
    ("interest", "Интерес: бизнес", 4),
    ("interest", "Интерес: ИИ агенты", 5),
    ("interest", "Интерес: автоматизация", 5),
    ("interest", "Интерес: программирование", 4),
    ("interest", "Интерес: тренировки и спорт", 4),
    ("interest", "Интерес: предпринимательство", 5),
    ("project", "Проект: Chief", 5),
    ("project", "Проект: Telegram AI Agent", 5),
    ("goal", "Цель: заработать на автоматизации бизнеса", 5),
    ("goal", "Цель: построить полезного AI агента", 5),
    ("goal", "Цель: монетизировать AI агента", 5),
    ("goal", "Цель: научиться создавать автономных агентов", 4),
    ("goal", "Цель: запустить коммерческие AI решения для бизнеса", 5),
    ("preference", "Предпочтение: короткие ответы по умолчанию", 5),
    ("preference", "Предпочтение: практические советы", 5),
    ("preference", "Предпочтение: реальные кейсы", 4),
    ("preference", "Предпочтение: фокус на пользе и заработке", 5),
)


class MemoryService:
    async def ensure_default_profile(self, user_id: int) -> None:
        async with AsyncSessionLocal() as session:
            existing = {
                row.content.strip().lower()
                for row in (
                    await session.execute(
                        select(MemoryItem).where(
                            MemoryItem.user_id == user_id,
                            MemoryItem.category.in_({"profile", "interest", "project", "goal", "preference"}),
                        )
                    )
                )
                .scalars()
                .all()
            }
            created = 0
            for category, content, importance in DEFAULT_USER_MEMORIES:
                if content.strip().lower() in existing:
                    continue
                session.add(
                    MemoryItem(
                        user_id=user_id,
                        content=content[:300],
                        category=category,
                        importance=importance,
                    )
                )
                created += 1
            if created:
                await session.commit()
                logger.info("default user memory seeded", user_id=user_id, created=created)

    async def remember(self, user_id: int, content: str, category: str = "manual", importance: int = 4) -> MemoryItem:
        clean = " ".join((content or "").split()).strip()
        if not clean:
            raise ValueError("memory content is empty")
        async with AsyncSessionLocal() as session:
            item = MemoryItem(
                user_id=user_id,
                content=clean[:300],
                category=(category or "manual")[:64],
                importance=max(1, min(5, importance)),
            )
            session.add(item)
            await session.commit()
            await session.refresh(item)
        logger.info("user memory stored", user_id=user_id, memory_id=item.id, category=item.category)
        return item

    async def forget(self, user_id: int, query: str) -> int:
        clean = " ".join((query or "").split()).strip().casefold()
        if not clean:
            return 0
        async with AsyncSessionLocal() as session:
            rows = (
                await session.execute(
                    select(MemoryItem).where(MemoryItem.user_id == user_id)
                )
            ).scalars().all()
            ids = [item.id for item in rows if clean in (item.content or "").casefold()]
            if not ids:
                return 0
            await session.execute(delete(MemoryItem).where(MemoryItem.id.in_(ids), MemoryItem.user_id == user_id))
            await session.commit()
        logger.info("user memory deleted", user_id=user_id, deleted=len(ids))
        return len(ids)

    async def list_items(self, user_id: int, limit: int = 40) -> list[MemoryItem]:
        await self.ensure_default_profile(user_id)
        async with AsyncSessionLocal() as session:
            rows = (
                await session.execute(
                    select(MemoryItem)
                    .where(MemoryItem.user_id == user_id)
                    .order_by(MemoryItem.importance.desc(), MemoryItem.created_at.desc())
                    .limit(limit)
                )
            ).scalars().all()
        return list(rows)

    async def context_for_user(self, user_id: int, limit: int = 30) -> str:
        rows = await self.list_items(user_id, limit=limit)
        if not rows:
            return ""
        grouped: dict[str, list[str]] = defaultdict(list)
        for item in rows:
            grouped[item.category or "general"].append(item.content)

        order = ["profile", "interest", "project", "goal", "preference", "manual", "general"]
        lines: list[str] = []
        for category in order + sorted(set(grouped) - set(order)):
            values = grouped.get(category)
            if not values:
                continue
            lines.append(f"{category}:")
            lines.extend(f"- {value}" for value in values[:8])
        return "\n".join(lines)

    async def recall_reply(self, user_id: int) -> str:
        rows = await self.list_items(user_id, limit=60)
        if not rows:
            return "Пока ничего устойчивого не помню."
        grouped: dict[str, list[str]] = defaultdict(list)
        for item in rows:
            grouped[item.category or "general"].append(item.content)
        parts: list[str] = ["Помню о тебе:"]
        for category in ["profile", "interest", "project", "goal", "preference", "manual", "general"]:
            values = grouped.get(category)
            if not values:
                continue
            parts.append("")
            parts.append(category.capitalize())
            parts.extend(f"• {value}" for value in values[:10])
        return "\n".join(parts).strip()
