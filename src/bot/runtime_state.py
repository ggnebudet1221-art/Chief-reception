from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

_STATE: dict[str, Any] = {
    "enabled": False,
    "running": False,
    "polling_started": False,
    "agents": {},
    "last_error": "",
    "updated_at": "",
}


def set_telegram_state(**updates: Any) -> None:
    _STATE.update(updates)
    _STATE["updated_at"] = datetime.now(timezone.utc).isoformat()


def set_agent_state(agent_id: str, **updates: Any) -> None:
    agents = dict(_STATE.get("agents") or {})
    current = dict(agents.get(agent_id) or {})
    current.update(updates)
    current["updated_at"] = datetime.now(timezone.utc).isoformat()
    agents[agent_id] = current
    _STATE["agents"] = agents
    _STATE["updated_at"] = datetime.now(timezone.utc).isoformat()


def get_telegram_state() -> dict[str, Any]:
    return dict(_STATE)
