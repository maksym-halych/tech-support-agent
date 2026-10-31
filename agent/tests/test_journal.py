from pathlib import Path

from tech_support_agent.journal import ActionJournal
from tech_support_agent.models import ApiResponse


def test_unfinished_dispatch_is_not_reclaimed_after_restart(tmp_path: Path) -> None:
    path = tmp_path / "actions.sqlite"
    journal = ActionJournal(path)
    assert journal.claim("action", "fingerprint") is None
    journal.close()
    restored = ActionJournal(path)
    assert restored.claim("action", "fingerprint").outcome == "unknown_outcome"
    restored.close()


def test_completed_dispatch_replays_receipt(tmp_path: Path) -> None:
    journal = ActionJournal(tmp_path / "actions.sqlite")
    assert journal.claim("action", "fingerprint") is None
    result = ApiResponse(outcome="success", data={"id": "booking-1"})
    journal.complete("action", result)
    assert journal.claim("action", "fingerprint") == result
    journal.close()
