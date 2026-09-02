from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass

from src.infrastructure.logging.logger import get_logger
from src.bot.runtime_state import set_agent_state, set_telegram_state
from src.services.agents import AgentDefinition, AgentRegistry

logger = get_logger(__name__)


def _mask_token(token: str) -> str:
    value = (token or "").strip()
    if not value:
        return "no"
    return f"yes:*{value[-6:]}"


@dataclass
class BotRuntime:
    agent: AgentDefinition
    bot: object
    bot_id: int
    scheduler: object | None = None
    proactive_scheduler: object | None = None


@dataclass
class TelegramBotManager:
    _runtimes: list[BotRuntime] | None = None
    _dispatcher: object | None = None
    _scheduler: object | None = None

    async def run_forever(self) -> None:
        registry = AgentRegistry()
        agents = registry.enabled_bots()
        set_telegram_state(
            enabled=bool(agents),
            running=False,
            polling_started=False,
            last_error="",
            agents={},
        )
        for agent in registry.all():
            set_agent_state(agent.id, name=agent.name, token_configured=bool(agent.bot_token), prepared=False, polling_started=False)
            logger.info(
                "Telegram agent token status",
                agent=agent.id,
                name=agent.name,
                token_configured=bool(agent.bot_token),
                token=_mask_token(agent.bot_token),
            )
        if not agents:
            set_telegram_state(enabled=False, running=False, polling_started=False, last_error="no per-agent bot tokens")
            logger.info("Telegram per-agent bot tokens are empty; communication layer disabled")
            return

        try:
            from aiogram.client.session.aiohttp import AiohttpSession
            from aiogram.exceptions import TelegramNetworkError
            from aiogram.utils.backoff import BackoffConfig
        except ModuleNotFoundError as error:
            raise RuntimeError(
                "aiogram is required for Telegram communication layer. "
                "Install backend dependencies from requirements.txt."
            ) from error

        from src.core.config import get_settings
        from src.bot.app import build_bot, build_dispatcher
        from src.bot.handlers.chat import set_reminder_scheduler
        from src.bot.workspace_publisher import clear_workspace_bots, register_workspace_bot
        from src.services.proactive.scheduler_service import ProactiveChiefSchedulerService
        from src.services.reminders.scheduler_service import ReminderSchedulerService

        settings = get_settings()
        backoff = BackoffConfig(min_delay=1.0, max_delay=60.0, factor=1.7, jitter=0.25)
        self._runtimes = []
        clear_workspace_bots()
        agent_by_bot_id: dict[int, str] = {}
        agent_name_by_bot_id: dict[int, str] = {}
        for agent in agents:
            session = AiohttpSession(
                proxy=settings.telegram_proxy_url or None,
                timeout=settings.telegram_request_timeout,
            )
            bot = build_bot(session=session, token=agent.bot_token)
            logger.info(f"[{agent.name}] deleting webhook before polling", agent=agent.id)
            set_agent_state(agent.id, name=agent.name, token_configured=True, prepared=False, polling_started=False, stage="delete_webhook")
            await bot.delete_webhook(drop_pending_updates=False)
            bot_info = await bot.get_me()
            agent_by_bot_id[bot_info.id] = agent.id
            agent_name_by_bot_id[bot_info.id] = agent.name
            logger.info(
                f"[{agent.name}] bot identity loaded",
                agent=agent.id,
                bot_id=bot_info.id,
                username=bot_info.username,
            )
            logger.info(
                "Telegram bot prepared",
                agent=agent.id,
                name=agent.name,
                bot_id=bot_info.id,
            )
            set_agent_state(
                agent.id,
                name=agent.name,
                token_configured=True,
                prepared=True,
                polling_started=False,
                bot_id=bot_info.id,
                username=bot_info.username,
                stage="prepared",
            )
            register_workspace_bot(agent.id, bot)
            scheduler = ReminderSchedulerService(bot) if agent.id == "chief" else None
            proactive_scheduler = ProactiveChiefSchedulerService(bot) if agent.id == "chief" else None
            runtime = BotRuntime(
                agent=agent,
                bot=bot,
                bot_id=bot_info.id,
                scheduler=scheduler,
                proactive_scheduler=proactive_scheduler,
            )
            self._runtimes.append(runtime)

        self._dispatcher = build_dispatcher(
            agent_id="chief",
            agent_name="Chief",
            agent_by_bot_id=agent_by_bot_id,
            agent_name_by_bot_id=agent_name_by_bot_id,
        )
        logger.info(
            "Telegram dispatcher prepared",
            allowed_updates=self._dispatcher.resolve_used_update_types(),
            bot_ids=sorted(agent_by_bot_id.keys()),
        )

        chief_runtime = next((runtime for runtime in self._runtimes if runtime.agent.id == "chief"), self._runtimes[0])
        if chief_runtime.scheduler is not None:
            self._scheduler = chief_runtime.scheduler
            set_reminder_scheduler(chief_runtime.scheduler)

        logger.info(
            "Telegram multi-bot aiogram communication layer starting",
            bots=[runtime.agent.id for runtime in self._runtimes],
            polling_timeout=settings.telegram_polling_timeout,
            request_timeout=settings.telegram_request_timeout,
            proxy_configured=bool(settings.telegram_proxy_url),
        )
        set_telegram_state(enabled=True, running=True, polling_started=False, last_error="")
        if self._scheduler is not None:
            await self._scheduler.start()
        for runtime in self._runtimes:
            if runtime.proactive_scheduler is not None:
                await runtime.proactive_scheduler.start()

        while True:
            try:
                for runtime in self._runtimes:
                    logger.info(f"[{runtime.agent.name}] polling started", agent=runtime.agent.id, bot_id=runtime.bot_id)
                    set_agent_state(runtime.agent.id, polling_started=True, stage="polling")
                set_telegram_state(enabled=True, running=True, polling_started=True, last_error="")
                await self._dispatcher.start_polling(
                    *[runtime.bot for runtime in self._runtimes],
                    polling_timeout=settings.telegram_polling_timeout,
                    backoff_config=backoff,
                    allowed_updates=self._dispatcher.resolve_used_update_types(),
                )
                logger.warning("Telegram shared polling returned unexpectedly")
            except asyncio.CancelledError:
                raise
            except TelegramNetworkError as error:
                set_telegram_state(running=True, polling_started=False, last_error=f"network: {error}")
                logger.warning("Telegram network error; reconnecting", error=str(error))
                await asyncio.sleep(5)
            except Exception as error:
                set_telegram_state(running=True, polling_started=False, last_error=f"{type(error).__name__}: {error}")
                logger.exception("Telegram polling crashed; reconnecting", error=str(error))
                await asyncio.sleep(10)

    async def shutdown(self) -> None:
        from src.bot.workspace_publisher import clear_workspace_bots

        logger.info("Telegram communication layer stopping")
        set_telegram_state(running=False, polling_started=False)
        clear_workspace_bots()
        if self._scheduler is not None:
            await self._scheduler.shutdown()
        for runtime in self._runtimes or []:
            if runtime.proactive_scheduler is not None:
                with contextlib.suppress(Exception):
                    await runtime.proactive_scheduler.shutdown()
        if self._dispatcher is not None:
            with contextlib.suppress(Exception):
                await self._dispatcher.stop_polling()
        for runtime in self._runtimes or []:
            with contextlib.suppress(Exception):
                await runtime.bot.session.close()
