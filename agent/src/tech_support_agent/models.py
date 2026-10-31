"""Typed boundaries for the support graph and its HTTP integration."""

from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, JsonValue, SecretStr, field_validator


class BoundaryModel(BaseModel):
    """Reject misspelled or unexpected fields at public boundaries."""

    model_config = ConfigDict(extra="forbid")


class ApiCredentials(BoundaryModel):
    """Credentials supplied by the authenticated caller, never by the model."""

    bearer_token: SecretStr | None = Field(default=None, exclude=True, repr=False)
    api_keys: dict[str, SecretStr] = Field(
        default_factory=dict, exclude=True, repr=False
    )


class SupportContext(BoundaryModel):
    """Trusted per-invocation context; the caller owns authentication."""

    user_id: str = Field(min_length=1)
    timezone: str = "Europe/Kyiv"
    credentials: ApiCredentials = Field(
        default_factory=ApiCredentials, exclude=True, repr=False
    )

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        """Check that the context uses an IANA timezone."""

        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("Unknown IANA timezone") from exc
        return value


class ApiConfig(BoundaryModel):
    """One trusted API and local specification per graph instance."""

    openapi_path: Path
    base_url: str
    idempotency_header: str | None = None

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        """Require an explicit HTTP origin without embedded credentials."""

        url = urlsplit(value)
        if (
            url.scheme not in {"http", "https"}
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
        ):
            raise ValueError(
                "API base_url must be HTTP(S), without credentials/query/fragment"
            )
        return value.rstrip("/")

    @field_validator("idempotency_header")
    @classmethod
    def validate_idempotency_header(cls, value: str | None) -> str | None:
        """Allow only an ordinary application header name."""

        import re

        if value is not None and (
            not re.fullmatch(r"[A-Za-z0-9-]+", value)
            or value.lower() in {"authorization", "host", "cookie", "content-type"}
        ):
            raise ValueError("Invalid idempotency header")
        return value


class ApiRequest(BoundaryModel):
    """Operation arguments, with no URL, method, or authentication override."""

    operation_id: str = Field(min_length=1)
    path_parameters: dict[str, JsonValue] = Field(default_factory=dict)
    query_parameters: dict[str, JsonValue] = Field(default_factory=dict)
    header_parameters: dict[str, JsonValue] = Field(default_factory=dict)
    body: JsonValue = None


class ApiResponse(BoundaryModel):
    """Sanitized HTTP result suitable for model messages and checkpoints."""

    outcome: Literal["success", "failed", "unknown_outcome"]
    status_code: int | None = None
    data: JsonValue = None
    error: str | None = None


class SupportResult(BoundaryModel):
    """Machine-readable handoff to an orchestrator."""

    status: Literal[
        "completed", "needs_input", "unsupported", "failed", "unknown_outcome"
    ]
    message: str
    data: JsonValue = None


class PendingAction(BoundaryModel):
    """Frozen, credential-free request awaiting approval."""

    action_id: str
    request: ApiRequest
    fingerprint: str
    summary: str
    tool_call_id: str
    integration_fingerprint: str


class ApprovalDecision(BoundaryModel):
    """Only an exact action ID and an explicit decision can resume a write."""

    action_id: str
    decision: Literal["approve", "reject"]


class OperationSummary(BoundaryModel):
    """Compact catalog entry exposed to the model."""

    operation_id: str
    method: str
    path: str
    summary: str
    supported: bool
    unsupported_reason: str | None = None


class OperationDetails(OperationSummary):
    """Resolved request and response documentation."""

    description: str = ""
    parameters: list[dict[str, JsonValue]] = Field(default_factory=list)
    request_body: dict[str, JsonValue] | None = None
    responses: dict[str, JsonValue] = Field(default_factory=dict)
    security: list[dict[str, list[str]]] = Field(default_factory=list)


# Add these types to a strict JsonPlusSerializer on the parent/standalone saver.
CHECKPOINT_TYPES = (ApiRequest, ApiResponse, PendingAction, SupportResult)
