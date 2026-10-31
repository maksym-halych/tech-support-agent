"""Durable dispatch receipts prevent replaying interrupted writes."""

import sqlite3
from pathlib import Path

from tech_support_agent.models import ApiResponse


class ActionJournal:
    """Claim a write before HTTP; an unfinished claim means unknown outcome."""

    def __init__(self, path: Path | str = ":memory:") -> None:
        """Open a durable journal, or a process-local journal for isolated tests."""

        self.connection = sqlite3.connect(str(path), check_same_thread=False, timeout=1)
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS actions (action_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, response TEXT)"
        )
        self.connection.commit()

    def claim(self, action_id: str, fingerprint: str) -> ApiResponse | None:
        """Return the previous outcome, or None for the first dispatch attempt."""

        with self.connection:
            inserted = self.connection.execute(
                "INSERT OR IGNORE INTO actions(action_id, fingerprint) VALUES (?, ?)",
                (action_id, fingerprint),
            )
            if inserted.rowcount:
                return None
            previous = self.connection.execute(
                "SELECT fingerprint, response FROM actions WHERE action_id = ?",
                (action_id,),
            ).fetchone()
        if previous[0] != fingerprint:
            raise ValueError("Action ID belongs to a different request or user")
        if previous[1]:
            return ApiResponse.model_validate_json(previous[1])
        return ApiResponse(
            outcome="unknown_outcome",
            error="This action was already dispatched; reconcile its outcome before another write",
        )

    def complete(self, action_id: str, response: ApiResponse) -> None:
        """Persist the sanitized outcome without credentials or request data."""

        with self.connection:
            self.connection.execute(
                "UPDATE actions SET response = ? WHERE action_id = ?",
                (response.model_dump_json(), action_id),
            )

    def close(self) -> None:
        """Release the SQLite connection owned by this journal."""

        self.connection.close()
