import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI

from tech_support_agent.models import SupportContext
from tech_support_agent.tools.get_current_datetime import get_current_datetime


@pytest.mark.parametrize("timezone", ["Europe/Kyiv", "America/New_York", "UTC"])
def test_current_datetime_uses_context_timezone(timezone: str) -> None:
    result = get_current_datetime(timezone)
    assert result.timezone == timezone
    returned = datetime.fromisoformat(result.datetime)
    assert abs(datetime.now(ZoneInfo(timezone)) - returned) < timedelta(seconds=5)


def test_invalid_timezone_is_rejected() -> None:
    with pytest.raises(ValueError, match="timezone"):
        SupportContext(user_id="alice", timezone="invalid")


def test_demo_contract_matches_exported_document(demo_app: FastAPI) -> None:
    document = Path(__file__).parents[1] / "examples" / "lessons.openapi.json"
    assert json.loads(document.read_text(encoding="utf-8")) == demo_app.openapi()
