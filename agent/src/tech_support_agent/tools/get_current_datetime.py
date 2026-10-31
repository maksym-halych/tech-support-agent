"""Tool: the current date and time in the user's timezone."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from langchain.tools import ToolRuntime
from langchain_core.tools import tool
from pydantic import BaseModel

from tech_support_agent.context import AgentContext


class CurrentDatetime(BaseModel):
    """The current time, local and UTC."""

    local: str
    weekday: str
    timezone: str
    utc: str


@tool
def get_current_datetime(runtime: ToolRuntime[AgentContext]) -> str:
    """
    Get the current date and time in the user's timezone.

    Use it to resolve relative dates such as "tomorrow" or "next Monday" when
    the date in the system prompt is not enough, e.g. in a long conversation.

    Returns:
        JSON with the local ISO 8601 time, weekday, timezone and UTC time.
    """

    context = runtime.context or AgentContext()
    now = datetime.now(UTC)
    local = now.astimezone(ZoneInfo(context.timezone))
    return CurrentDatetime(
        local=local.isoformat(timespec="minutes"),
        weekday=local.strftime("%A"),
        timezone=context.timezone,
        utc=now.isoformat(timespec="minutes"),
    ).model_dump_json()
