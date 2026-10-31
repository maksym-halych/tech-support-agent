"""Models mirroring the lessons API, and the views the tools return."""

from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict

LessonStatus = Literal["open", "booked", "cancelled"]


class ApiModel(BaseModel):
    """A lessons API response; unknown fields are ignored for forward compatibility."""

    model_config = ConfigDict(extra="ignore", frozen=True)


class Person(ApiModel):
    """A lesson participant."""

    id: str
    name: str


class Teacher(ApiModel):
    """A teacher who has signed in at least once."""

    id: str
    name: str


class Lesson(ApiModel):
    """A lesson as the API returns it, with UTC times."""

    id: str
    subject: str
    starts_at: datetime
    ends_at: datetime
    duration_minutes: int
    status: LessonStatus
    teacher: Person
    student: Person | None
    cancelled_at: datetime | None = None


class LessonView(BaseModel):
    """A lesson as the model sees it: local times next to the UTC start."""

    id: str
    subject: str
    status: LessonStatus
    teacher: str
    student: str | None
    day: str
    time: str
    duration_minutes: int
    starts_at_local: str
    starts_at_utc: str

    @classmethod
    def of(cls, lesson: Lesson, timezone: ZoneInfo) -> LessonView:
        """
        Present a lesson in the user's timezone.

        Args:
            lesson: The lesson from the API.
            timezone: The user's timezone.

        Returns:
            The view with local day, time range and ISO start.
        """

        start = lesson.starts_at.astimezone(timezone)
        end = lesson.ends_at.astimezone(timezone)
        return cls(
            id=lesson.id,
            subject=lesson.subject,
            status=lesson.status,
            teacher=lesson.teacher.name,
            student=lesson.student.name if lesson.student else None,
            day=start.strftime("%A, %d %B %Y"),
            time=f"{start:%H:%M}–{end:%H:%M}",
            duration_minutes=lesson.duration_minutes,
            starts_at_local=start.isoformat(timespec="minutes"),
            starts_at_utc=lesson.starts_at.isoformat(timespec="minutes"),
        )
