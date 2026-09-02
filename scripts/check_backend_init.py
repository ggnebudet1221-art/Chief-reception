import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
shared_site_packages = Path(r"C:\Users\Public\AIManagerVenv\Lib\site-packages")
if shared_site_packages.exists():
    sys.path.insert(0, str(shared_site_packages))

from src.api.app import app
from src.infrastructure.db.session import init_db


async def main() -> None:
    await init_db()
    routes = sorted({route.path for route in app.routes if route.path.startswith("/api/")})
    print("DB_INIT_OK")
    for route in routes:
        print(route)


if __name__ == "__main__":
    asyncio.run(main())
