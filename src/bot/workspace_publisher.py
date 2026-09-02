from __future__ import annotations

import asyncio

from aiogram import Bot

from src.bot.telegram_formatting import telegram_html_chunks
from src.core.config import get_settings
from src.infrastructure.logging.logger import get_logger
from src.services.agents.orchestrator import WorkspaceEvent

logger = get_logger(__name__)

_AGENT_BOTS: dict[str, Bot] = {}


def register_workspace_bot(agent_id: str, bot: Bot) -> None:
    _AGENT_BOTS[agent_id] = bot
    logger.info("Telegram workspace publisher bot registered", agent=agent_id)


def clear_workspace_bots() -> None:
    _AGENT_BOTS.clear()


class TelegramWorkspacePublisher:
    def __init__(self, bot: Bot) -> None:
        self.bot = bot

    async def publish(self, event: WorkspaceEvent) -> None:
        settings = get_settings()
        chat_id = settings.telegram_coordination_chat_id
        if not chat_id:
            logger.info(
                "Telegram workspace event skipped; TELEGRAM_COORDINATION_CHAT_ID is empty",
                channel=event.channel,
                sender=event.sender,
                task_id=event.task_id,
            )
            return

        text = self._format_event(event)
        if not text.strip():
            return

        topic_id = self._topic_id(event.channel)
        agent_id = self._agent_id_for_event(event)
        bot = self._bot_for_event(event)
        chunks = telegram_html_chunks(text, max_len=1200)
        for index, chunk in enumerate(chunks, start=1):
            kwargs = {"chat_id": chat_id, "text": chunk}
            if topic_id:
                kwargs["message_thread_id"] = topic_id
            await self._send_with_retry(bot, kwargs, event=event, agent_id=agent_id, chunk_index=index)
            logger.info(
                "Telegram workspace event sent",
                channel=event.channel,
                sender=event.sender,
                bot_agent=agent_id,
                task_id=event.task_id,
                chunk_index=index,
                chunk_count=len(chunks),
                topic_id=topic_id,
            )

    def _topic_id(self, channel: str) -> int:
        settings = get_settings()
        if channel == "tasks":
            return settings.telegram_tasks_topic_id
        if channel == "infra":
            return settings.telegram_infra_topic_id
        return settings.telegram_general_topic_id

    def _bot_for_event(self, event: WorkspaceEvent) -> Bot:
        agent_id = self._agent_id_for_event(event)
        bot = _AGENT_BOTS.get(agent_id)
        if bot is not None:
            return bot
        if event.channel == "general" and agent_id != "chief":
            raise RuntimeError(f"Telegram bot for worker agent is not registered: {agent_id}")
        logger.warning(
            "Telegram workspace publisher missing agent bot; using fallback bot",
            sender=event.sender,
            agent_id=agent_id,
            channel=event.channel,
        )
        return self.bot

    async def _send_with_retry(
        self,
        bot: Bot,
        kwargs: dict,
        *,
        event: WorkspaceEvent,
        agent_id: str,
        chunk_index: int,
    ) -> None:
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                await bot.send_message(**kwargs)
                return
            except Exception as error:
                last_error = error
                logger.exception(
                    "Telegram workspace event send failed",
                    channel=event.channel,
                    sender=event.sender,
                    bot_agent=agent_id,
                    task_id=event.task_id,
                    chunk_index=chunk_index,
                    attempt=attempt + 1,
                    chat_id=kwargs.get("chat_id"),
                    topic_id=kwargs.get("message_thread_id"),
                )
                if attempt == 0:
                    await asyncio.sleep(1)
        if last_error is not None:
            raise last_error

    def _agent_id_for_event(self, event: WorkspaceEvent) -> str:
        if event.sender_agent_id:
            return event.sender_agent_id
        sender = (event.sender or "").strip().lower()
        if sender == "business":
            return "business"
        if sender == "smm":
            return "smm"
        return "chief"

    def _format_event(self, event: WorkspaceEvent) -> str:
        if event.channel == "tasks":
            lines = [f"TASK #{event.task_id}" if event.task_id else "TASK"]
            if event.target:
                lines.append(f"{event.sender} -> {event.target}")
            lines.append(f"state={event.status.lower()}")
            if event.text:
                lines.append(f"goal={event.text}")
            return "\n".join(lines)

        if event.channel == "infra":
            lines = [f"[{event.status.lower()}]"]
            if event.task_id:
                lines.append(f"task={event.task_id}")
            if event.sender and event.sender != "SYSTEM":
                lines.append(f"agent={event.sender}")
            lines.append(event.text)
            return "\n".join(lines)

        return event.text
