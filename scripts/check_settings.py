import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHARED_SITE_PACKAGES = Path(r"C:\Users\Public\AIManagerVenv\Lib\site-packages")

sys.path.insert(0, str(ROOT))
if SHARED_SITE_PACKAGES.exists():
    sys.path.insert(0, str(SHARED_SITE_PACKAGES))

from src.core.config import get_settings


def mask(token: str) -> str:
    token = (token or "").strip()
    return "no" if not token else f"yes:*{token[-6:]}"


settings = get_settings()
print(f"ENABLE_TELEGRAM_BOT={settings.enable_telegram_bot}")
print(f"CHIEF_BOT_TOKEN={mask(settings.chief_bot_token)}")
print(f"BUSINESS_BOT_TOKEN={mask(settings.business_bot_token)}")
print(f"SMM_BOT_TOKEN={mask(settings.smm_bot_token)}")
print(f"TELEGRAM_BOT_TOKEN={mask(settings.telegram_bot_token)}")
print(f"OWNER_TELEGRAM_ID={settings.owner_telegram_id}")
