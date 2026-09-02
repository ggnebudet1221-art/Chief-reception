from __future__ import annotations

import asyncio
import contextlib
import sys
from pathlib import Path


SHARED_SITE_PACKAGES = Path(r"C:\Users\Public\AIManagerVenv\Lib\site-packages")


def _bootstrap_shared_dependencies() -> None:
    if SHARED_SITE_PACKAGES.exists():
        path = str(SHARED_SITE_PACKAGES)
        if path not in sys.path:
            sys.path.insert(0, path)


_bootstrap_shared_dependencies()


def _mask_token(token: str) -> str:
    value = (token or "").strip()
    if not value:
        return "no"
    return f"yes:*{value[-6:]}"


async def _run_telegram_runtime() -> None:
    from src.infrastructure.logging.logger import get_logger
    from src.bot.runtime_state import set_telegram_state

    logger = get_logger(__name__)
    while True:
        try:
            from src.bot.manager import TelegramBotManager
        except ModuleNotFoundError as error:
            set_telegram_state(enabled=False, running=False, polling_started=False, last_error=str(error))
            logger.error("Telegram communication layer cannot start: dependency missing", error=str(error))
            return

        manager = TelegramBotManager()
        try:
            await manager.run_forever()
        except asyncio.CancelledError:
            raise
        except RuntimeError as error:
            set_telegram_state(running=False, polling_started=False, last_error=str(error))
            logger.error("Telegram communication layer cannot start; retrying", error=str(error))
        except Exception as error:
            set_telegram_state(running=False, polling_started=False, last_error=f"{type(error).__name__}: {error}")
            logger.exception("Telegram communication layer crashed; retrying", error=str(error))
        finally:
            await manager.shutdown()
        await asyncio.sleep(10)


async def main() -> None:
    try:
        import uvicorn

        from src.api.app import app
        from src.core.config import get_settings
        from src.infrastructure.db.session import init_db
        from src.infrastructure.logging.logger import configure_logging, get_logger
    except (ImportError, ModuleNotFoundError) as error:
        from src.dev_backend import run_dev_backend

        run_dev_backend(import_error=error)
        return

    settings = get_settings()
    configure_logging(settings.log_level)
    logger = get_logger(__name__)
    await init_db()

    config = uvicorn.Config(app=app, host=settings.app_host, port=settings.app_port, log_config=None)
    server = uvicorn.Server(config)

    telegram_task: asyncio.Task | None = None
    telegram_tokens = [
        settings.chief_bot_token,
        settings.business_bot_token,
        settings.smm_bot_token,
    ]
    logger.info(
        "Telegram bootstrap config",
        enabled=settings.enable_telegram_bot,
        chief_token=_mask_token(settings.chief_bot_token),
        business_token=_mask_token(settings.business_bot_token),
        smm_token=_mask_token(settings.smm_bot_token),
        legacy_token=_mask_token(settings.telegram_bot_token),
        owner_telegram_id=settings.owner_telegram_id,
    )
    logger.info("[SERPER]", key_loaded=bool(settings.serper_api_key))
    if settings.enable_telegram_bot and any(token.strip() for token in telegram_tokens):
        telegram_task = asyncio.create_task(_run_telegram_runtime())
        telegram_task.add_done_callback(
            lambda task: None
            if task.cancelled()
            else (
                print(f"TELEGRAM_RUNTIME_TASK_FAILED: {task.exception()}", flush=True)
                if task.exception()
                else print("TELEGRAM_RUNTIME_TASK_EXITED", flush=True)
            )
        )
        logger.info("Telegram runtime task created")
    else:
        logger.warning("Telegram runtime not started: disabled or all per-agent tokens are empty")

    try:
        await server.serve()
    finally:
        if telegram_task is not None:
            telegram_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await telegram_task


if __name__ == "__main__":
    asyncio.run(main())
