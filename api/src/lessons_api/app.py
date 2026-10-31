"""FastAPI application factory."""

from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Annotated
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, Header, HTTPException, Query

from lessons_api.auth import current_user
from lessons_api.models import TIMEZONE, Booking, CreateBooking, Slot, Teacher
from lessons_api.storage import BookingStore, SlotTakenError

TEACHERS = [
    Teacher(id="iryna", name="Ірина Коваль", subject="English"),
    Teacher(id="oleh", name="Олег Мельник", subject="English"),
]


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


def create_app(
    database: Path | str = ":memory:", clock: Callable[[], datetime] | None = None
) -> FastAPI:
    """
    Create an isolated service with injectable storage and clock.

    Args:
        database: SQLite file path, or ``:memory:`` for tests.
        clock: Returns the current time; defaults to the system clock in UTC.

    Returns:
        The FastAPI app. ``app.state.store`` holds the store; close it on shutdown.
    """

    now = clock if clock is not None else lambda: datetime.now(UTC)
    store = BookingStore(database)
    app = FastAPI(
        title="Lesson booking demo",
        version="1.0.0",
        description="Find teachers, look up free slots by an exact local date, then book a selected slot for the authenticated user. All times use Europe/Kyiv. Reuse Idempotency-Key only for an identical booking request.",
    )
    app.openapi_version = "3.1.0"
    app.state.store = store

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

        occupied = store.booked_slot_ids()
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

        previous = store.find_by_key(user, idempotency_key)
        if previous is not None:
            if previous.slot.id != request.slot_id:
                raise HTTPException(409, "Idempotency key belongs to a different slot")
            return previous
        try:
            _, day, _ = request.slot_id.split(":")
            slot = next(
                slot
                for slot in _slots(date.fromisoformat(day), now())
                if slot.id == request.slot_id
            )
        except ValueError, StopIteration:
            raise HTTPException(404, "Slot does not exist or is in the past") from None
        booking = Booking(id=str(uuid4()), slot=slot)
        try:
            store.insert(user, idempotency_key, booking)
        except SlotTakenError:
            raise HTTPException(409, "Slot is already booked") from None
        return booking

    @app.get(
        "/me/bookings",
        operation_id="list_my_bookings",
        summary="List the current user's bookings",
    )
    def list_bookings(user: Annotated[str, Depends(current_user)]) -> list[Booking]:
        """Return only bookings belonging to the authenticated user."""

        return store.list_for_user(user)

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

        booking = store.get_for_user(booking_id, user)
        if booking is None:
            raise HTTPException(404, "Booking not found")
        return booking

    return app
