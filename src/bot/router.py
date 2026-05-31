from aiogram import Router

from src.bot.handlers.chat import router as chat_router
from src.bot.handlers.start import router as start_router


def get_bot_router() -> Router:
    router = Router()
    router.include_router(start_router)
    router.include_router(chat_router)
    return router
