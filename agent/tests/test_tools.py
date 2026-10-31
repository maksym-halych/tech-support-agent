from datetime import UTC, datetime, timedelta

from tech_support_agent.tools.get_current_datetime import get_current_datetime
from tech_support_agent.tools.registry import TOOLS


def test_registry_names_are_unique() -> None:
    names = [tool.name for tool in TOOLS]

    assert len(names) == len(set(names))


def test_get_current_datetime_returns_utc_now() -> None:
    returned = datetime.fromisoformat(get_current_datetime.invoke({}))

    assert returned.tzinfo is not None
    assert abs(datetime.now(UTC) - returned) < timedelta(seconds=5)
