from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy import text

from src.core.config import get_settings
from src.infrastructure.db.base import Base

settings = get_settings()

engine = create_async_engine(settings.database_url, echo=False, future=True)
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db_session() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session


def _import_all_models() -> None:
    from src.infrastructure.db.models import memory  # noqa: F401


async def init_db() -> None:
    _import_all_models()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _migrate_sqlite_tasks(conn)


async def init_models() -> None:
    await init_db()


async def _migrate_sqlite_tasks(conn) -> None:
    if not settings.database_url.startswith("sqlite"):
        return

    result = await conn.execute(text("PRAGMA table_info(tasks)"))
    existing = {row[1] for row in result.fetchall()}
    columns = {
        "task_type": "VARCHAR(32) DEFAULT 'user_task'",
        "assigned_to": "VARCHAR(64) DEFAULT 'Chief'",
        "assigned_agent": "VARCHAR(64) DEFAULT 'Chief'",
        "created_by": "VARCHAR(64) DEFAULT 'user'",
        "source": "VARCHAR(64) DEFAULT 'manual'",
        "description": "TEXT DEFAULT ''",
        "action_log": "TEXT DEFAULT ''",
        "current_step": "VARCHAR(300) DEFAULT ''",
        "result": "TEXT DEFAULT ''",
        "context": "TEXT DEFAULT ''",
        # SQLite cannot add a column with a non-constant default such as CURRENT_TIMESTAMP.
        # Add nullable datetime columns first, then backfill below.
        "updated_at": "DATETIME",
        "completed_at": "DATETIME",
    }
    for name, ddl in columns.items():
        if name not in existing:
            await conn.execute(text(f"ALTER TABLE tasks ADD COLUMN {name} {ddl}"))

    await conn.execute(text("UPDATE tasks SET task_type = 'user_task' WHERE task_type IS NULL OR task_type = ''"))
    await conn.execute(text("UPDATE tasks SET assigned_to = 'Chief' WHERE assigned_to IS NULL OR assigned_to = ''"))
    await conn.execute(text("UPDATE tasks SET assigned_agent = assigned_to WHERE assigned_agent IS NULL OR assigned_agent = ''"))
    await conn.execute(text("UPDATE tasks SET created_by = 'user' WHERE created_by IS NULL OR created_by = ''"))
    await conn.execute(text("UPDATE tasks SET source = 'manual' WHERE source IS NULL OR source = ''"))
    await conn.execute(text("UPDATE tasks SET description = '' WHERE description IS NULL"))
    await conn.execute(text("UPDATE tasks SET action_log = '' WHERE action_log IS NULL"))
    await conn.execute(text("UPDATE tasks SET current_step = '' WHERE current_step IS NULL"))
    await conn.execute(text("UPDATE tasks SET result = '' WHERE result IS NULL"))
    await conn.execute(text("UPDATE tasks SET context = '' WHERE context IS NULL"))
    await conn.execute(text("UPDATE tasks SET updated_at = CURRENT_TIMESTAMP WHERE updated_at IS NULL"))
    await conn.execute(text("UPDATE tasks SET status = 'active' WHERE status IN ('pending')"))
    await conn.execute(text("UPDATE tasks SET status = 'in_progress' WHERE status IN ('thinking', 'working', 'waiting')"))
    await conn.execute(text("UPDATE tasks SET status = 'completed' WHERE status IN ('done')"))
