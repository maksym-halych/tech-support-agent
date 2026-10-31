"""Pydantic schemas exchanged over HTTP."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

TIMEZONE = "Europe/Kyiv"


class BoundaryModel(BaseModel):
    """Reject misspelled or unexpected fields at public boundaries."""

    model_config = ConfigDict(extra="forbid")


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
