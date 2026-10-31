"""Runtime configuration for the agent."""

import re
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
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


class DemoSettings(BaseSettings):
    """Operator-only demo coordinates; no guessed repository or directory."""

    model_config = SettingsConfigDict(
        env_prefix="BUG_", env_file=".env", extra="ignore", hide_input_in_errors=True
    )

    demo_repo: str = Field(
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*$"
    )
    demo_repo_dir: Path
    sandbox_image: str = "bug-resolution-sandbox:phase2"

    @field_validator("demo_repo_dir")
    @classmethod
    def absolute_directory(cls, value: Path) -> Path:
        """Require a deliberately selected absolute filesystem path."""

        if not value.is_absolute() or ".." in value.parts:
            raise ValueError("BUG_DEMO_REPO_DIR must be an absolute path")
        return value

    @field_validator("sandbox_image")
    @classmethod
    def local_image_name(cls, value: str) -> str:
        """Reject flags and shell-like image values before Docker invocation."""

        if not re.fullmatch(r"[a-z0-9][a-z0-9./_:@-]{0,200}", value):
            raise ValueError("Invalid sandbox image reference")
        return value


class InvestigationSettings(BaseSettings):
    """Explicit Phase 3 provider, private Telegram target, and hard budgets."""

    model_config = SettingsConfigDict(
        env_prefix="BUG_", env_file=".env", extra="ignore", hide_input_in_errors=True
    )

    model: str = Field(min_length=1, max_length=200)
    model_api_key: SecretStr
    model_base_url: Literal["https://openrouter.ai/api/v1"] = (
        "https://openrouter.ai/api/v1"
    )
    telegram_bot_token: SecretStr
    telegram_user_id: int = Field(gt=0, strict=False)
    telegram_chat_id: int = Field(gt=0, strict=False)
    artifacts_dir: Path
    model_turns: int = Field(default=20, gt=0, le=100)
    stage_tool_calls: int = Field(default=30, gt=0, le=100)
    stage_seconds: float = Field(default=300, gt=0, le=600)
    reviewer_tool_calls: int = Field(default=15, gt=0, le=100)
    reviewer_seconds: float = Field(default=180, gt=0, le=600)

    @field_validator("model_api_key", "telegram_bot_token")
    @classmethod
    def nonempty_secret(cls, value: SecretStr) -> SecretStr:
        """Reject absent credentials without displaying their values."""

        if not value.get_secret_value().strip():
            raise ValueError("Configured credentials are required")
        return value

    @field_validator("artifacts_dir")
    @classmethod
    def trusted_artifact_directory(cls, value: Path) -> Path:
        """Require an existing operator-owned regular directory, never a model path."""

        if (
            not value.is_absolute()
            or not value.is_dir()
            or any(path.is_symlink() for path in (value, *value.parents))
        ):
            raise ValueError(
                "BUG_ARTIFACTS_DIR must be an existing absolute regular directory"
            )
        return value


class PublicationSettings(BaseSettings):
    """Explicit worker-only publication opt-in; absent credentials disable remote writes."""

    model_config = SettingsConfigDict(
        env_prefix="BUG_", env_file=".env", extra="ignore", hide_input_in_errors=True
    )
    publication_authorized: bool = False
    github_token: SecretStr = SecretStr("")
    github_workflow_id: int | None = Field(default=None, gt=0)
    ci_poll_seconds: float = Field(default=10, ge=0.1, le=300)

    @model_validator(mode="after")
    def authorized_configuration(self) -> PublicationSettings:
        """Require explicit credentials and exact workflow when live writes are enabled."""

        if self.publication_authorized and (
            not self.github_token.get_secret_value().strip()
            or self.github_workflow_id is None
        ):
            raise ValueError(
                "Authorized publication requires GitHub token and workflow ID"
            )
        return self
