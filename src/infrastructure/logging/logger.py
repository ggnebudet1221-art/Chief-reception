import logging
import sys
from pathlib import Path

import structlog


def configure_logging(log_level: str = "INFO") -> None:
    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            timestamper,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    log_dir = Path(r"C:\Users\Public\AIManagerLogs")
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_dir / "backend-runtime.log", encoding="utf-8"))
    except OSError:
        pass

    logging.basicConfig(
        format="%(message)s",
        handlers=handlers,
        level=getattr(logging, log_level.upper(), logging.INFO),
        force=True,
    )


def get_logger(name: str):
    return structlog.get_logger(name)
