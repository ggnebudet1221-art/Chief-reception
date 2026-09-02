import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHARED_SITE_PACKAGES = Path(r"C:\Users\Public\AIManagerVenv\Lib\site-packages")

sys.path.insert(0, str(ROOT))
if SHARED_SITE_PACKAGES.exists():
    sys.path.insert(0, str(SHARED_SITE_PACKAGES))

from src.core.config import get_settings


def get(path: str, token: str | None = None):
    req = urllib.request.Request(f"http://127.0.0.1:8000{path}")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=5) as response:
        body = response.read().decode("utf-8")
        return response.status, body


settings = get_settings()
for path, auth in [
    ("/health", False),
    ("/api/agents", True),
    ("/api/tasks/queue", True),
    ("/api/system/stats", True),
    ("/api/agents/activity", True),
]:
    status, body = get(path, settings.web_access_token if auth else None)
    preview = body
    try:
        parsed = json.loads(body)
        if isinstance(parsed, list):
            preview = f"list[{len(parsed)}]"
        elif isinstance(parsed, dict):
            preview = f"dict keys={sorted(parsed.keys())}"
    except json.JSONDecodeError:
        preview = preview[:80]
    print(f"{path} -> {status} {preview}")
