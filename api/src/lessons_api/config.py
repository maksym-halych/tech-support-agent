"""Runtime configuration for the lessons API."""

from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


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

    # Clerk frontend API URL, e.g. https://example-app.clerk.accounts.dev. It
    # is the token issuer, and the JWKS lives at {issuer}/.well-known/jwks.json.
    clerk_issuer: str
    # Origins allowed in the token's ``azp`` claim; empty skips the check.
    clerk_authorized_parties: Annotated[list[str], NoDecode] = []
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:3000"]

    @field_validator("clerk_authorized_parties", "cors_origins", mode="before")
    @classmethod
    def split_comma_separated(cls, value: str | list[str]) -> list[str]:
        """Accept a comma-separated string from the environment."""

        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("clerk_issuer")
    @classmethod
    def strip_trailing_slash(cls, value: str) -> str:
        """Compare issuers without a trailing slash, as Clerk issues them."""

        return value.rstrip("/")


@lru_cache
def get_settings() -> Settings:
    """
    Return the cached settings instance.

    Returns:
        The process-wide Settings, built on first call.
    """

    return Settings()
