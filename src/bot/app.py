from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware, Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import TelegramObject, Update

from src.bot.router import get_bot_router
from src.infrastructure.logging.logger import get_logger

logger = get_logger(__name__)

WORKER_ALLOWED_COMMANDS = ("/start", "/debug_chat_ids", "/debugids")


class AgentContextMiddleware(BaseMiddleware):
    def __init__(
        self,
        default_agent_id: str = "chief",
        default_agent_name: str = "Chief",
        agent_by_bot_id: dict[int, str] | None = None,
        agent_name_by_bot_id: dict[int, str] | None = None,
    ) -> None:
        self.default_agent_id = default_agent_id
        self.default_agent_name = default_agent_name
        self.agent_by_bot_id = agent_by_bot_id or {}
        self.agent_name_by_bot_id = agent_name_by_bot_id or {}

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        bot = data.get("bot")
        bot_id = getattr(bot, "id", None)
        agent_id = self.agent_by_bot_id.get(bot_id, self.default_agent_id)
        agent_name = self.agent_name_by_bot_id.get(bot_id, self.default_agent_name)
        data["bot_agent_id"] = agent_id
        details = {}
        if isinstance(event, Update):
            details["update_id"] = event.update_id
            if event.message:
                details["chat_id"] = event.message.chat.id if event.message.chat else None
                details["user_id"] = event.message.from_user.id if event.message.from_user else None
                details["text"] = event.message.text
        logger.info(f"[{agent_name}] update received", agent=agent_id, bot_id=bot_id, **details)
        if agent_id != "chief" and self._is_raw_telegram_input(event):
            logger.info(
                f"[{agent_name}] raw Telegram input ignored; worker agents consume internal tasks only",
                agent=agent_id,
                bot_id=bot_id,
                **details,
            )
            return None
        return await handler(event, data)

    def _is_raw_telegram_input(self, event: TelegramObject) -> bool:
        if not isinstance(event, Update):
            return False
        if event.callback_query:
            message = event.callback_query.message
            if message and message.chat and message.chat.type == "private":
                return False
            return True
        if not event.message:
            return False
        if event.message.chat and event.message.chat.type == "private":
            return False
        text = (event.message.text or "").strip()
        if self._is_allowed_worker_command(event.message):
            return False
        return True

    def _is_allowed_worker_command(self, message: Any) -> bool:
        text = (message.text or "").strip()
        command = text.split(maxsplit=1)[0].split("@", 1)[0].lower()
        if command in WORKER_ALLOWED_COMMANDS:
            return True
        for entity in message.entities or []:
            if getattr(entity, "type", None) == "bot_command" and getattr(entity, "offset", None) == 0:
                raw = text[: getattr(entity, "length", 0)].split("@", 1)[0].lower()
                return raw in WORKER_ALLOWED_COMMANDS
        return False


def build_dispatcher(
    agent_id: str = "chief",
    agent_name: str | None = None,
    agent_by_bot_id: dict[int, str] | None = None,
    agent_name_by_bot_id: dict[int, str] | None = None,
) -> Dispatcher:
    dispatcher = Dispatcher()
    dispatcher.update.middleware(
        AgentContextMiddleware(
            default_agent_id=agent_id,
            default_agent_name=agent_name or agent_id,
            agent_by_bot_id=agent_by_bot_id,
            agent_name_by_bot_id=agent_name_by_bot_id,
        )
    )
    dispatcher.include_router(get_bot_router())
    return dispatcher


def build_bot(session: BaseSession | None = None, token: str | None = None) -> Bot:
    if not token:
        raise RuntimeError("Bot token is required for aiogram runtime")
    return Bot(
        token=token,
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
