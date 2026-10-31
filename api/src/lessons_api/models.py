"""Pydantic schemas exchanged over HTTP."""

from datetime import datetime, timedelta
from typing import Annotated, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    computed_field,
)

Role = Literal["student", "teacher"]
LessonStatus = Literal["open", "booked", "cancelled"]

MIN_DURATION_MINUTES = 15
MAX_DURATION_MINUTES = 240


class BoundaryModel(BaseModel):
    """Reject misspelled or unexpected fields at public boundaries."""

    model_config = ConfigDict(extra="forbid")


class Caller(BoundaryModel):
    """The authenticated user, taken from verified Clerk token claims."""

    id: str
    name: str
    email: str
    role: Role


class Person(BoundaryModel):
    """Another participant of a lesson, as much as the caller may see."""

    id: str
    name: str


class Teacher(BoundaryModel):
    """A teacher who has signed in at least once."""

    id: str
    name: str


class Lesson(BoundaryModel):
    """A lesson slot opened by a teacher, optionally booked by a student."""

    id: str
    subject: str
    starts_at: datetime = Field(description="Start time in UTC")
    duration_minutes: int
    status: LessonStatus
    teacher: Person
    student: Person | None
    created_at: datetime
    cancelled_at: datetime | None

    @computed_field(description="End time in UTC")
    @property
    def ends_at(self) -> datetime:
        """The lesson's end time."""

        return self.starts_at + timedelta(minutes=self.duration_minutes)


class CreateLesson(BoundaryModel):
    """A new open lesson; the teacher is the caller."""

    subject: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)
    ]
    starts_at: AwareDatetime = Field(
        description="Start time with an explicit UTC offset; must be in the future"
    )
    duration_minutes: int = Field(
        default=60, ge=MIN_DURATION_MINUTES, le=MAX_DURATION_MINUTES
    )
