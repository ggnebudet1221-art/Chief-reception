import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHARED_SITE_PACKAGES = Path(r"C:\Users\Public\AIManagerVenv\Lib\site-packages")

sys.path.insert(0, str(ROOT))
if SHARED_SITE_PACKAGES.exists():
    sys.path.insert(0, str(SHARED_SITE_PACKAGES))

runpy.run_module("src.main", run_name="__main__")
