from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message

from src.bot.telegram_formatting import send_telegram_chunks
from src.infrastructure.logging.logger import get_logger

router = Router()
logger = get_logger(__name__)


START_MESSAGES = {
    "chief": (
        "Привет. Я Chief.\n"
        "Управляю задачами, агентами и приоритетами.\n"
        "Могу собрать команду под задачу, делегировать работу и выдать короткое решение."
    ),
    "business": (
        "Business Agent на связи.\n"
        "Отвечаю за монетизацию, стратегию, MVP, unit-экономику и риски запуска."
    ),
    "smm": (
        "SMM Agent на связи.\n"
        "Отвечаю за контент, hooks, упаковку, tone of voice и growth-механики."
    ),
}


@router.message(CommandStart())
async def start_handler(message: Message, bot_agent_id: str = "chief") -> None:
    logger.info(
        f"[{bot_agent_id}] handler entered",
        agent=bot_agent_id,
        command="start",
        chat_id=message.chat.id if message.chat else None,
        user_id=message.from_user.id if message.from_user else None,
        text=message.text,
    )
    await send_telegram_chunks(
        message.answer,
        START_MESSAGES.get(bot_agent_id, START_MESSAGES["chief"]),
        logger=logger,
        agent=bot_agent_id,
        kind="start",
    )
    logger.info(f"[{bot_agent_id}] reply sent", agent=bot_agent_id, kind="start")
