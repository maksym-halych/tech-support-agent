"""Runtime configuration for the agent."""

from functools import lru_cache
from typing import Annotated

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """Settings loaded from the environment or a local .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # A model id as the provider at ``openai_base_url`` names it: bare on
    # OpenAI, the default, ``<provider>/<model>`` on OpenRouter.
    agent_model: str = "gpt-6.1-sol"

    # Any provider that speaks the OpenAI chat completions API: OpenAI by
    # default, or https://openrouter.ai/api/v1, or a local server. The key is
    # that provider's. Secret.
    openai_api_key: SecretStr = SecretStr("")
    openai_base_url: str = "https://api.openai.com/v1"

    # Clerk frontend API URL, e.g. https://example-app.clerk.accounts.dev: the
    # session token issuer, with its JWKS at {issuer}/.well-known/jwks.json.
    clerk_issuer: str = ""
    # Origins allowed in the token's ``azp`` claim; empty skips the check.
    clerk_authorized_parties: Annotated[list[str], NoDecode] = []

    # The lessons API the agent's tools call as the signed-in user.
    lessons_api_url: str = "http://127.0.0.1:8001"

    @field_validator("clerk_authorized_parties", mode="before")
    @classmethod
    def split_comma_separated(cls, value: str | list[str]) -> list[str]:
        """Accept a comma-separated string from the environment."""

        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("clerk_issuer", "lessons_api_url")
    @classmethod
    def strip_trailing_slash(cls, value: str) -> str:
        """Store URLs without a trailing slash."""

        return value.rstrip("/")


@lru_cache
def get_settings() -> Settings:
    """
    Return the cached settings instance.

    Returns:
        The process-wide Settings, built on first call.
    """

    return Settings()
