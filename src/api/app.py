from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from src.api.routes.health import router as health_router
from src.api.routes.web_chat import router as web_chat_router
from src.api.routes.web_memories import router as web_memories_router
from src.api.routes.web_plan import router as web_plan_router
from src.api.routes.web_profile import router as web_profile_router
from src.api.routes.web_reminders import router as web_reminders_router
from src.api.routes.web_status import router as web_status_router
from src.api.routes.web_tasks import router as web_tasks_router
from src.core.config import get_settings
from src.core.lifecycle import lifespan

WEB_DIR = Path(__file__).resolve().parents[2] / "web"


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(WEB_DIR / "index.html")

    app.include_router(health_router)
    app.include_router(web_chat_router)
    app.include_router(web_tasks_router)
    app.include_router(web_plan_router)
    app.include_router(web_reminders_router)
    app.include_router(web_profile_router)
    app.include_router(web_memories_router)
    app.include_router(web_status_router)
    return app


app = create_app()
