"""Per-run inputs the agent trusts: the run context and the signed-in user."""

from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from langgraph.runtime import ServerInfo
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

DEFAULT_TIMEZONE = "Europe/Kyiv"


class AgentContext(BaseModel):
    """Run context the frontend sends with every run (``context`` in the SDK)."""

    model_config = ConfigDict(extra="ignore")

    timezone: str = Field(
        default=DEFAULT_TIMEZONE,
        description="The user's IANA timezone, used for every date and time",
    )

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        """Accept only IANA timezone names."""

        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise ValueError(f"Unknown timezone {value!r}") from error
        return value


class SignedInUser(BaseModel):
    """The user Agent Server authenticated for this run."""

    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    role: Literal["student", "teacher"]
    # The caller's Clerk session token, forwarded to the lessons API. Never
    # shown to the model, never part of graph state.
    token: SecretStr = Field(exclude=True, repr=False)


def _field(user: Any, key: str) -> Any:
    try:
        return user[key]
    except KeyError, TypeError:
        return None


def signed_in_user(server_info: ServerInfo | None) -> SignedInUser | None:
    """
    Read the Clerk-authenticated user Agent Server attached to the run.

    Args:
        server_info: ``runtime.server_info``; None outside Agent Server.

    Returns:
        The user, or None when the run has no Clerk user (tests, Studio).
    """

    user = server_info.user if server_info is not None else None
    if user is None:
        return None
    token = _field(user, "access_token")
    if not token:
        return None
    return SignedInUser(
        id=user.identity,
        name=user.display_name,
        role="teacher" if _field(user, "role") == "teacher" else "student",
        token=SecretStr(token),
    )
