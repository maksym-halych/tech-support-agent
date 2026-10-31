"""Runtime configuration for the agent."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    support_openapi_path: Path = Path("examples/lessons.openapi.json")
    support_api_base_url: str = "http://127.0.0.1:8001"
    support_idempotency_header: str | None = "Idempotency-Key"
    support_user_id: str = "alice"
    support_timezone: str = "Europe/Kyiv"
    support_api_token: SecretStr = SecretStr("demo-alice")
    support_api_keys: dict[str, SecretStr] = Field(default_factory=dict)
    support_checkpoint_path: Path = Path("data/support.sqlite")
    support_journal_path: Path = Path("data/actions.sqlite")

    @field_validator("support_idempotency_header", mode="before")
    @classmethod
    def empty_header_means_disabled(cls, value: str | None) -> str | None:
        """Allow an empty environment variable to disable idempotency headers."""

        return value or None


@lru_cache
def get_settings() -> Settings:
    """
    Return the cached settings instance.

    Returns:
        The process-wide Settings, built on first call.
    """

    return Settings()
