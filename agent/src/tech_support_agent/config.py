"""Runtime configuration for the agent."""

from functools import lru_cache

from pydantic import SecretStr
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


@lru_cache
def get_settings() -> Settings:
    """
    Return the cached settings instance.

    Returns:
        The process-wide Settings, built on first call.
    """

    return Settings()
