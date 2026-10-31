"""The original demo booking API, kept as a test fixture for the generic OpenAPI stack.

The real lessons API lives in ``api/``. This module goes away with the generic
stack in task 3 (see ``docs/tasks/03-agent-server.md``).
"""

import sqlite3
from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from threading import Lock
from typing import Annotated
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import Field

from tech_support_agent.models import BoundaryModel

TIMEZONE = "Europe/Kyiv"
bearer = HTTPBearer()


class Teacher(BoundaryModel):
    """A demo teacher."""

    id: str
    name: str
    subject: str


class Slot(BoundaryModel):
    """A lesson with an explicit date, time and timezone."""

    id: str
    teacher_id: str
    teacher_name: str
    starts_at: datetime
    ends_at: datetime
    timezone: str = TIMEZONE


class CreateBooking(BoundaryModel):
    """Selected slot; identity comes only from the authenticated token."""

    slot_id: str = Field(min_length=1)


class Booking(BoundaryModel):
    """A confirmed booking belonging to the current user."""

    id: str
    slot: Slot


TEACHERS = [
    Teacher(id="iryna", name="Ірина Коваль", subject="English"),
    Teacher(id="oleh", name="Олег Мельник", subject="English"),
]


def current_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(bearer)],
) -> str:
    """Authenticate the two explicitly documented local demo identities."""

    users = {"demo-alice": "alice", "demo-bob": "bob"}
    if credentials.credentials not in users:
        raise HTTPException(401, "Unknown demo token")
    return users[credentials.credentials]


def _slots(lesson_date: date, now: datetime) -> list[Slot]:
    if lesson_date.weekday() >= 5:
        return []
    slots = []
    for teacher in TEACHERS:
        for hour in (10, 14, 16):
            start = datetime.combine(lesson_date, time(hour), ZoneInfo(TIMEZONE))
            if start > now:
                slots.append(
                    Slot(
                        id=f"{teacher.id}:{lesson_date.isoformat()}:{hour:02d}",
                        teacher_id=teacher.id,
                        teacher_name=teacher.name,
                        starts_at=start,
                        ends_at=start + timedelta(hours=1),
                    )
                )
    return slots


def create_demo_app(
    database: Path | str = ":memory:", clock: Callable[[], datetime] | None = None
) -> FastAPI:
    """Create an isolated service with injectable storage and clock."""

    now = clock if clock is not None else lambda: datetime.now(UTC)
    connection = sqlite3.connect(str(database), check_same_thread=False)
    connection.execute(
        "CREATE TABLE IF NOT EXISTS bookings (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, slot_id TEXT UNIQUE NOT NULL, idempotency_key TEXT NOT NULL, payload TEXT NOT NULL, UNIQUE(user_id, idempotency_key))"
    )
    connection.commit()
    lock = Lock()
    app = FastAPI(
        title="Lesson booking demo",
        version="1.0.0",
        description="Find teachers, look up free slots by an exact local date, then book a selected slot for the authenticated user. All times use Europe/Kyiv. Reuse Idempotency-Key only for an identical booking request.",
    )
    app.openapi_version = "3.1.0"
    app.state.connection = connection

    @app.get(
        "/teachers",
        operation_id="list_teachers",
        summary="List English lesson teachers",
    )
    def list_teachers(user: Annotated[str, Depends(current_user)]) -> list[Teacher]:
        """Return the teachers available in the demo."""

        return TEACHERS

    @app.get(
        "/slots",
        operation_id="list_available_slots",
        summary="Find available slots on an exact date",
    )
    def list_slots(
        lesson_date: Annotated[
            date,
            Query(
                alias="date",
                description="Exact calendar date in Europe/Kyiv, YYYY-MM-DD",
            ),
        ],
        user: Annotated[str, Depends(current_user)],
        teacher_id: str | None = None,
    ) -> list[Slot]:
        """Return future unbooked slots; compare starts_at to the requested time."""

        with lock:
            occupied = {
                row[0] for row in connection.execute("SELECT slot_id FROM bookings")
            }
        return [
            slot
            for slot in _slots(lesson_date, now())
            if slot.id not in occupied
            and (teacher_id is None or slot.teacher_id == teacher_id)
        ]

    @app.post(
        "/me/bookings",
        operation_id="create_booking",
        summary="Book a selected slot for the current user",
        status_code=201,
        responses={
            409: {
                "description": "Slot is already booked or the idempotency key was used for another slot"
            },
            404: {"description": "The slot does not exist or is in the past"},
        },
    )
    def create_booking(
        request: CreateBooking,
        user: Annotated[str, Depends(current_user)],
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1)],
    ) -> Booking:
        """Create one booking; replaying the same key returns the original booking."""

        with lock, connection:
            previous = connection.execute(
                "SELECT slot_id, payload FROM bookings WHERE user_id = ? AND idempotency_key = ?",
                (user, idempotency_key),
            ).fetchone()
            if previous:
                if previous[0] != request.slot_id:
                    raise HTTPException(
                        409, "Idempotency key belongs to a different slot"
                    )
                return Booking.model_validate_json(previous[1])
            try:
                _, day, _ = request.slot_id.split(":")
                slot = next(
                    slot
                    for slot in _slots(date.fromisoformat(day), now())
                    if slot.id == request.slot_id
                )
            except ValueError, StopIteration:
                raise HTTPException(
                    404, "Slot does not exist or is in the past"
                ) from None
            booking = Booking(id=str(uuid4()), slot=slot)
            try:
                connection.execute(
                    "INSERT INTO bookings VALUES (?, ?, ?, ?, ?)",
                    (
                        booking.id,
                        user,
                        slot.id,
                        idempotency_key,
                        booking.model_dump_json(),
                    ),
                )
            except sqlite3.IntegrityError:
                raise HTTPException(409, "Slot is already booked") from None
            return booking

    @app.get(
        "/me/bookings",
        operation_id="list_my_bookings",
        summary="List the current user's bookings",
    )
    def list_bookings(user: Annotated[str, Depends(current_user)]) -> list[Booking]:
        """Return only bookings belonging to the authenticated user."""

        with lock:
            return [
                Booking.model_validate_json(row[0])
                for row in connection.execute(
                    "SELECT payload FROM bookings WHERE user_id = ? ORDER BY slot_id",
                    (user,),
                )
            ]

    @app.get(
        "/me/bookings/{booking_id}",
        operation_id="get_my_booking",
        summary="Get a confirmed booking by ID",
        responses={404: {"description": "Booking not found for the current user"}},
    )
    def get_booking(
        booking_id: str, user: Annotated[str, Depends(current_user)]
    ) -> Booking:
        """Look up one booking without revealing other users' records."""

        with lock:
            row = connection.execute(
                "SELECT payload FROM bookings WHERE id = ? AND user_id = ?",
                (booking_id, user),
            ).fetchone()
        if row is None:
            raise HTTPException(404, "Booking not found")
        return Booking.model_validate_json(row[0])

    return app
