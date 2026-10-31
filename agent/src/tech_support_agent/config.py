"""Runtime configuration for the agent."""

from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit

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


@lru_cache
def get_settings() -> Settings:
    """
    Return the cached settings instance.

    Returns:
        The process-wide Settings, built on first call.
    """

    return Settings()


class BugSettings(BaseSettings):
    """Server-only configuration, independent of the general support model."""

    model_config = SettingsConfigDict(
        env_prefix="BUG_", env_file=".env", extra="ignore", hide_input_in_errors=True
    )

    worker_url: str = "http://127.0.0.1:2025"
    worker_token: SecretStr
    scenario: Literal["continue", "both"] = "continue"
    request_timeout: float = Field(default=2, gt=0, le=10)
    poll_timeout: float = Field(default=5, gt=0, le=30)
    poll_interval: float = Field(default=0.1, gt=0, le=1)

    @field_validator("worker_url")
    @classmethod
    def loopback_only(cls, value: str) -> str:
        """Reject remote endpoints and URL credentials before sending a token."""

        url = urlsplit(value)
        if (
            url.scheme != "http"
            or url.hostname != "127.0.0.1"
            or not url.port
            or url.username
            or url.password
            or url.path not in ("", "/")
            or url.query
            or url.fragment
        ):
            raise ValueError("Worker URL must be http://127.0.0.1:<port>")
        return value.rstrip("/")

    @field_validator("worker_token")
    @classmethod
    def token_required(cls, value: SecretStr) -> SecretStr:
        """Require a nontrivial operator-provided bearer token."""

        token = value.get_secret_value()
        if len(token) < 32 or not token.isascii() or any(c.isspace() for c in token):
            raise ValueError(
                "BUG_WORKER_TOKEN must contain at least 32 ASCII characters"
            )
        return value
