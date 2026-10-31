"""Tool: the current date and time, for reasoning about timestamps in logs and errors."""

from datetime import UTC, datetime

from langchain_core.tools import tool


@tool
def get_current_datetime() -> str:
    """
    Get the current date and time in UTC.

    Use it when the user's problem depends on the current time: an expired
    certificate, a log timestamp, or how long ago something happened.

    Returns:
        The current UTC time in ISO 8601 format.
    """

    return datetime.now(UTC).isoformat(timespec="seconds")
