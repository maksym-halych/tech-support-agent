"""Runtime configuration for the lessons API."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Settings loaded from ``LESSONS_API_*`` environment variables or ``.env``."""

    model_config = SettingsConfigDict(
        env_prefix="LESSONS_API_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    host: str = "127.0.0.1"
    port: int = 8001
    database_path: Path = Path("data/lessons.sqlite")


@lru_cache
def get_settings() -> Settings:
    """
    Return the cached settings instance.

    Returns:
        The process-wide Settings, built on first call.
    """

    return Settings()
