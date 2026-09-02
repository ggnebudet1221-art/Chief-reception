from __future__ import annotations

from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

SERVER_TZ_NAME = "Europe/Moscow"

try:
    SERVER_TZ = ZoneInfo(SERVER_TZ_NAME)
except ZoneInfoNotFoundError:
    SERVER_TZ = timezone(timedelta(hours=3), name="MSK")


def runtime_datetime_context() -> str:
    utc_now = datetime.now(timezone.utc)
    local_now = utc_now.astimezone(SERVER_TZ)
    return (
        "Runtime current date/time:\n"
        f"Current UTC time: {utc_now.strftime('%Y-%m-%dT%H:%M:%SZ')}\n"
        f"Server local time: {local_now.strftime('%Y-%m-%d %H:%M:%S')} {SERVER_TZ_NAME}\n"
        f"Current date: {local_now.date().isoformat()}\n"
        f"Current year: {local_now.year}\n"
        f"Timezone: {SERVER_TZ_NAME}\n"
        f"Readable datetime: {local_now.strftime('%A, %Y-%m-%d %H:%M:%S %Z')}"
    )
