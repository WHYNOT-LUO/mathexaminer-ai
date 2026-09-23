import io
import sys
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def make_image(size=(300, 200), color=(200, 30, 30), fmt="PNG", mode="RGB") -> bytes:
    buf = io.BytesIO()
    Image.new(mode, size, color).save(buf, format=fmt)
    return buf.getvalue()


def make_pdf(pages=1) -> bytes:
    imgs = [Image.new("RGB", (400, 560), (255, 255, 255)) for _ in range(pages)]
    buf = io.BytesIO()
    imgs[0].save(buf, format="PDF", save_all=True, append_images=imgs[1:])
    return buf.getvalue()


@pytest.fixture
def png_bytes():
    return make_image()


@pytest.fixture
def settings():
    from mathexaminer.config import Settings

    return Settings(
        supabase_url="https://x.supabase.co",
        supabase_key="anon",
        deepseek_api_key="SECRET-KEY-123",
        deepseek_model="deepseek-flash",
        deepseek_base_url="https://proxy.example.com/deepseek",
    )


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Tests must not pick up real credentials or overrides from the developer's shell."""
    for name in ("DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "DEEPSEEK_MODEL", "MAX_GRADINGS_PER_DAY",
                 "SUPABASE_URL", "SUPABASE_KEY"):
        monkeypatch.delenv(name, raising=False)
    # Ignore a developer's real .streamlit/secrets.toml: settings come from the environment only.
    import os

    from mathexaminer import config

    monkeypatch.setattr(config, "_lookup", os.environ.get)
