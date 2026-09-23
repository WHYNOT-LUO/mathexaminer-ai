"""Runtime configuration, read from Streamlit secrets or environment variables."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

DEFAULT_DEEPSEEK_MODEL = "deepseek-flash"  # deepseek-v4-pro does not support image input
DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com"

BUCKET_SCHEMES = "markschemes"
BUCKET_SUBMISSIONS = "submissions"

LOW_CONFIDENCE_THRESHOLD = 60
MODEL_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


class ConfigError(RuntimeError):
    """A required setting is missing or malformed."""


def _lookup(name: str) -> str | None:
    try:
        import streamlit as st

        if name in st.secrets:
            return str(st.secrets[name])
    except Exception:
        pass
    return os.environ.get(name)


@dataclass(frozen=True)
class Settings:
    supabase_url: str
    supabase_key: str
    deepseek_api_key: str
    deepseek_model: str = DEFAULT_DEEPSEEK_MODEL
    deepseek_base_url: str = DEFAULT_DEEPSEEK_BASE_URL
    max_gradings_per_day: int = 20

    def validate(self) -> Settings:
        if not MODEL_NAME_RE.match(self.deepseek_model):
            raise ConfigError(f"DEEPSEEK_MODEL {self.deepseek_model!r} is not a valid model id.")
        if not self.deepseek_base_url.startswith(("http://", "https://")):
            raise ConfigError("DEEPSEEK_BASE_URL must start with http:// or https://")
        return self


def load_settings() -> Settings:
    required = {
        "SUPABASE_URL": _lookup("SUPABASE_URL"),
        "SUPABASE_KEY": _lookup("SUPABASE_KEY"),
        "DEEPSEEK_API_KEY": _lookup("DEEPSEEK_API_KEY"),
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        raise ConfigError(
            "Missing configuration: " + ", ".join(missing)
            + ". Copy .streamlit/secrets.toml.example to .streamlit/secrets.toml and fill it in."
        )
    try:
        daily_cap = int(_lookup("MAX_GRADINGS_PER_DAY") or 20)
    except ValueError as exc:
        raise ConfigError("MAX_GRADINGS_PER_DAY must be an integer.") from exc
    return Settings(
        supabase_url=required["SUPABASE_URL"].strip(),
        supabase_key=required["SUPABASE_KEY"].strip(),
        deepseek_api_key=required["DEEPSEEK_API_KEY"].strip(),
        deepseek_model=(_lookup("DEEPSEEK_MODEL") or DEFAULT_DEEPSEEK_MODEL).strip(),
        deepseek_base_url=(_lookup("DEEPSEEK_BASE_URL") or DEFAULT_DEEPSEEK_BASE_URL).strip().rstrip("/"),
        max_gradings_per_day=daily_cap,
    ).validate()
