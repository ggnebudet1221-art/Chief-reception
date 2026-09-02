from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi import Request, Response
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from src.api.routes.health import router as health_router
from src.api.routes.web_chat import router as web_chat_router
from src.api.routes.web_agents import router as web_agents_router
from src.api.routes.web_memories import router as web_memories_router
from src.api.routes.web_plan import router as web_plan_router
from src.api.routes.web_profile import router as web_profile_router
from src.api.routes.web_reminders import router as web_reminders_router
from src.api.routes.web_status import router as web_status_router
from src.api.routes.web_system import router as web_system_router
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

    @app.middleware("http")
    async def no_frontend_cache(request: Request, call_next) -> Response:
        response = await call_next(request)
        if request.url.path == "/" or request.url.path.startswith("/static/") or request.url.path == "/manifest.json":
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(WEB_DIR / "index.html", headers={"Cache-Control": "no-store"})

    @app.get("/manifest.json")
    async def manifest() -> FileResponse:
        return FileResponse(WEB_DIR / "manifest.json", headers={"Cache-Control": "no-store"})

    @app.get("/static/service-worker.js")
    async def legacy_service_worker() -> PlainTextResponse:
        return PlainTextResponse(
            "self.addEventListener('install', () => self.skipWaiting());"
            "self.addEventListener('activate', event => event.waitUntil(self.registration.unregister()));",
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                "Service-Worker-Allowed": "/",
                "Clear-Site-Data": '"cache"',
            },
        )

    app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")

    app.include_router(health_router)
    app.include_router(web_chat_router)
    app.include_router(web_agents_router)
    app.include_router(web_tasks_router)
    app.include_router(web_plan_router)
    app.include_router(web_reminders_router)
    app.include_router(web_profile_router)
    app.include_router(web_memories_router)
    app.include_router(web_status_router)
    app.include_router(web_system_router)
    return app


app = create_app()
