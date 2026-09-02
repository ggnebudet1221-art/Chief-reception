import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHARED_SITE_PACKAGES = Path(r"C:\Users\Public\AIManagerVenv\Lib\site-packages")

sys.path.insert(0, str(ROOT))
if SHARED_SITE_PACKAGES.exists():
    sys.path.insert(0, str(SHARED_SITE_PACKAGES))

from aiogram.client.default import DefaultBotProperties
from aiogram import Bot
from aiogram.enums import ParseMode

from src.infrastructure.logging.logger import configure_logging
from src.services.agents.registry import AgentRegistry


def mask(token: str) -> str:
    token = (token or "").strip()
    return "no" if not token else f"yes:*{token[-6:]}"


async def main() -> None:
    configure_logging("INFO")
    registry = AgentRegistry()
    for agent in registry.all():
        print(f"[{agent.name}] token={mask(agent.bot_token)}")
        if not agent.bot_token:
            continue
        bot = Bot(token=agent.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        try:
            await bot.delete_webhook(drop_pending_updates=False)
            me = await bot.get_me()
            webhook = await bot.get_webhook_info()
            print(f"[{agent.name}] get_me ok id={me.id} username=@{me.username}")
            print(f"[{agent.name}] webhook url={webhook.url!r} pending={webhook.pending_update_count}")
        except Exception as error:
            print(f"[{agent.name}] telegram api failed: {type(error).__name__}: {error}")
            raise
        finally:
            await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
