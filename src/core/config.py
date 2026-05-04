from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT_DIR / "data"


class RateLimits:
    """Per-user anti-abuse limits enforced via Redis.

    Not a paywall — just protection so a single client cannot exhaust
    Tesseract or external APIs for everyone else.
    """

    TEXT_PER_HOUR = 60
    PHOTO_PER_10_MIN = 10
    PRODUCT_PER_HOUR = 30


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ROOT_DIR / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    bot_token: str = Field(default="")

    database_url: str = "postgresql+asyncpg://inci:inci@localhost:5432/inci"
    alembic_database_url: str = "postgresql+psycopg2://inci:inci@localhost:5432/inci"

    redis_url: str = "redis://localhost:6379/0"

    tesseract_cmd: str = ""
    tesseract_langs: str = "eng+rus"

    app_env: str = "production"
    log_level: str = "INFO"
    support_username: str = ""

    skinsignal_enabled: bool = True
    skinsignal_max_lookups: int = 6


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
