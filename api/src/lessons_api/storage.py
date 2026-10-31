"""SQLite persistence for bookings."""

import sqlite3
from pathlib import Path
from threading import Lock

from lessons_api.models import Booking

SCHEMA = """
CREATE TABLE IF NOT EXISTS bookings (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    slot_id TEXT UNIQUE NOT NULL,
    idempotency_key TEXT NOT NULL,
    payload TEXT NOT NULL,
    UNIQUE(user_id, idempotency_key)
)
"""


class SlotTakenError(Exception):
    """The slot already has a booking."""


class BookingStore:
    """Thread-safe access to the bookings table."""

    def __init__(self, database: Path | str = ":memory:") -> None:
        """
        Open the database and create the schema if needed.

        Args:
            database: SQLite file path, or ``:memory:`` for an isolated store.
        """

        self._connection = sqlite3.connect(str(database), check_same_thread=False)
        self._connection.execute(SCHEMA)
        self._connection.commit()
        self._lock = Lock()

    def close(self) -> None:
        """Close the underlying connection."""

        self._connection.close()

    def booked_slot_ids(self) -> set[str]:
        """
        Return the IDs of all booked slots.

        Returns:
            Slot IDs that already have a booking.
        """

        with self._lock:
            rows = self._connection.execute("SELECT slot_id FROM bookings")
            return {row[0] for row in rows}

    def find_by_key(self, user_id: str, idempotency_key: str) -> Booking | None:
        """
        Find a booking previously made with the same idempotency key.

        Args:
            user_id: The caller's user ID.
            idempotency_key: The ``Idempotency-Key`` header value.

        Returns:
            The earlier booking, or None if the key is unused.
        """

        with self._lock:
            row = self._connection.execute(
                "SELECT payload FROM bookings WHERE user_id = ? AND idempotency_key = ?",
                (user_id, idempotency_key),
            ).fetchone()
        return Booking.model_validate_json(row[0]) if row else None

    def insert(self, user_id: str, idempotency_key: str, booking: Booking) -> None:
        """
        Store a new booking.

        Args:
            user_id: The caller's user ID.
            idempotency_key: The ``Idempotency-Key`` header value.
            booking: The booking to store.

        Raises:
            SlotTakenError: If the slot or the key is already used.
        """

        try:
            with self._lock, self._connection:
                self._connection.execute(
                    "INSERT INTO bookings VALUES (?, ?, ?, ?, ?)",
                    (
                        booking.id,
                        user_id,
                        booking.slot.id,
                        idempotency_key,
                        booking.model_dump_json(),
                    ),
                )
        except sqlite3.IntegrityError:
            raise SlotTakenError(booking.slot.id) from None

    def list_for_user(self, user_id: str) -> list[Booking]:
        """
        Return the caller's bookings ordered by slot.

        Args:
            user_id: The caller's user ID.

        Returns:
            The caller's bookings.
        """

        with self._lock:
            rows = self._connection.execute(
                "SELECT payload FROM bookings WHERE user_id = ? ORDER BY slot_id",
                (user_id,),
            ).fetchall()
        return [Booking.model_validate_json(row[0]) for row in rows]

    def get_for_user(self, booking_id: str, user_id: str) -> Booking | None:
        """
        Return one of the caller's bookings.

        Args:
            booking_id: The booking ID.
            user_id: The caller's user ID.

        Returns:
            The booking, or None if it does not exist or belongs to someone else.
        """

        with self._lock:
            row = self._connection.execute(
                "SELECT payload FROM bookings WHERE id = ? AND user_id = ?",
                (booking_id, user_id),
            ).fetchone()
        return Booking.model_validate_json(row[0]) if row else None
