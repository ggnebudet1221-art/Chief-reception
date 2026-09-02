from fastapi import APIRouter
from src.bot.runtime_state import get_telegram_state

router = APIRouter(tags=["health"])


@router.get("/health", summary="Healthcheck")
async def healthcheck() -> dict:
    return {"status": "ok", "telegram": get_telegram_state()}


@router.get("/health/telegram", summary="Telegram polling health")
async def telegram_healthcheck() -> dict:
    state = get_telegram_state()
    ok = bool(state.get("running") and state.get("polling_started"))
    return {"status": "ok" if ok else "not_ready", **state}
