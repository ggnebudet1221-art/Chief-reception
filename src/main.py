import uvicorn

from src.api.app import app
from src.core.config import get_settings
from src.infrastructure.db.session import init_db
from src.infrastructure.logging.logger import configure_logging


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    await init_db()

    config = uvicorn.Config(app=app, host=settings.app_host, port=settings.app_port, log_config=None)
    server = uvicorn.Server(config)
    await server.serve()


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
