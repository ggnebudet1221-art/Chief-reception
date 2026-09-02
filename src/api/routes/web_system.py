from datetime import datetime, timezone
import ctypes
import ctypes.wintypes
import os
import shutil
import time

from fastapi import APIRouter, Depends
from sqlalchemy import select

from src.api.deps import require_token
from src.core.config import get_settings
from src.infrastructure.db.models.memory import Task
from src.infrastructure.db.session import AsyncSessionLocal

router = APIRouter(prefix="/api/system", tags=["web-system"], dependencies=[Depends(require_token)])
STARTED_AT = time.monotonic()
_CPU_SAMPLE: tuple[int, int] | None = None


class _MemoryStatus(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def _filetime_to_int(filetime: ctypes.Structure) -> int:
    return (filetime.dwHighDateTime << 32) + filetime.dwLowDateTime


def _windows_cpu_percent() -> float | None:
    global _CPU_SAMPLE

    if os.name != "nt":
        return None

    idle = ctypes.wintypes.FILETIME()
    kernel = ctypes.wintypes.FILETIME()
    user = ctypes.wintypes.FILETIME()
    ok = ctypes.windll.kernel32.GetSystemTimes(
        ctypes.byref(idle),
        ctypes.byref(kernel),
        ctypes.byref(user),
    )
    if not ok:
        return None

    idle_time = _filetime_to_int(idle)
    total_time = _filetime_to_int(kernel) + _filetime_to_int(user)
    previous = _CPU_SAMPLE
    _CPU_SAMPLE = (idle_time, total_time)
    if previous is None:
        return 0.0

    idle_delta = idle_time - previous[0]
    total_delta = total_time - previous[1]
    if total_delta <= 0:
        return 0.0
    return round(max(0.0, min(100.0, 100.0 * (1.0 - idle_delta / total_delta))), 1)


def _windows_memory_mb() -> tuple[int | None, int | None]:
    if os.name != "nt":
        return None, None

    status = _MemoryStatus()
    status.dwLength = ctypes.sizeof(_MemoryStatus)
    ok = ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
    if not ok:
        return None, None

    total = round(status.ullTotalPhys / 1024 / 1024)
    used = round((status.ullTotalPhys - status.ullAvailPhys) / 1024 / 1024)
    return used, total


@router.get("/stats")
async def system_stats() -> dict:
    settings = get_settings()
    async with AsyncSessionLocal() as session:
        active_tasks = len(
            (
                await session.execute(
                    select(Task).where(Task.user_id == settings.web_owner_id, Task.status == "active")
                )
            )
            .scalars()
            .all()
        )

    cpu_percent = _windows_cpu_percent() or 0.0
    memory_used_mb, memory_total_mb = _windows_memory_mb()

    disk = shutil.disk_usage(".")
    return {
        "cpu_percent": cpu_percent,
        "memory_used_mb": memory_used_mb,
        "memory_total_mb": memory_total_mb,
        "disk_used_gb": round(disk.used / 1024 / 1024 / 1024, 1),
        "disk_total_gb": round(disk.total / 1024 / 1024 / 1024, 1),
        "uptime_seconds": int(time.monotonic() - STARTED_AT),
        "tasks_running": active_tasks,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
