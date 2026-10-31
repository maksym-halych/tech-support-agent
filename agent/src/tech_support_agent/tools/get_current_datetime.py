"""Trusted local time for interpreting relative dates."""

from datetime import datetime
from zoneinfo import ZoneInfo

from tech_support_agent.models import BoundaryModel


class CurrentDatetime(BoundaryModel):
    """Current ISO timestamp and its IANA timezone."""

    datetime: str
    timezone: str


def get_current_datetime(timezone: str = "Europe/Kyiv") -> CurrentDatetime:
    """Return the current time in the supplied IANA timezone."""

    return CurrentDatetime(
        datetime=datetime.now(ZoneInfo(timezone)).isoformat(timespec="seconds"),
        timezone=timezone,
    )
