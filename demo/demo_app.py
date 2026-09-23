"""Run the full UI against an in-memory backend: `streamlit run demo/demo_app.py`."""

import os
import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "demo")]

os.environ.setdefault("SUPABASE_URL", "https://demo.invalid")
os.environ.setdefault("SUPABASE_KEY", "demo")
os.environ.setdefault("DEEPSEEK_API_KEY", "demo")

import fake_backend  # noqa: E402

fake_backend.install()

runpy.run_path(str(ROOT / "app.py"), run_name="__main__")
