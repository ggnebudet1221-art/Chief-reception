import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHARED_SITE_PACKAGES = Path(r"C:\Users\Public\AIManagerVenv\Lib\site-packages")

sys.path.insert(0, str(ROOT))
if SHARED_SITE_PACKAGES.exists():
    sys.path.insert(0, str(SHARED_SITE_PACKAGES))

from src.bot.manager import TelegramBotManager
from src.infrastructure.logging.logger import configure_logging


async def main() -> None:
    configure_logging("INFO")
    manager = TelegramBotManager()
    task = asyncio.create_task(manager.run_forever())
    print("POLLING_CHECK_STARTED", flush=True)
    await asyncio.sleep(20)
    print("POLLING_CHECK_CANCELLING", flush=True)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        print("POLLING_CHECK_CANCELLED", flush=True)
    await manager.shutdown()
    print("POLLING_CHECK_DONE", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
