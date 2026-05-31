from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI

from src.infrastructure.db.session import init_db
from src.infrastructure.logging.logger import get_logger

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    logger.info("Application startup started")
    await init_db()
    logger.info("Application startup completed")
    try:
        yield
    finally:
        logger.info("Application shutdown completed")
