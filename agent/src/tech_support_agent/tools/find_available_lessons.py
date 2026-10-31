"""Tool: open lessons a student can book."""

from datetime import date

from pydantic import BaseModel, Field, model_validator

from tech_support_agent.lessons.client import client_for
from tech_support_agent.tools.shared import (
    Runtime,
    call_api,
    day_window,
    lesson_tool,
    lessons_json,
    require_user,
    user_timezone,
)

MAX_DAYS = 31


class FindAvailableLessonsInput(BaseModel):
    """Input for find_available_lessons."""

    date_from: date = Field(description="First local day to search, YYYY-MM-DD")
    date_to: date | None = Field(
        default=None,
        description="Last local day to search, inclusive, YYYY-MM-DD; defaults to date_from",
    )
    teacher_id: str | None = Field(
        default=None,
        description="Only this teacher's lessons; an id from list_teachers",
    )

    @model_validator(mode="after")
    def check_range(self) -> FindAvailableLessonsInput:
        """Keep the search window ordered and at most a month long."""

        if self.date_to is not None:
            if self.date_to < self.date_from:
                raise ValueError("date_to must not be before date_from")
            if (self.date_to - self.date_from).days >= MAX_DAYS:
                raise ValueError(f"search at most {MAX_DAYS} days at a time")
        return self


@lesson_tool(FindAvailableLessonsInput)
async def find_available_lessons(
    runtime: Runtime,
    date_from: date,
    date_to: date | None = None,
    teacher_id: str | None = None,
) -> str:
    """
    Find open lessons the student can book on one or more local days.

    Show the options to the user with their local day and time, and let them choose before booking.

    Returns:
        JSON with the open lessons, times in the user's timezone.
    """

    user = require_user(runtime, "student")
    timezone = user_timezone(runtime)
    start, end = day_window(date_from, date_to, timezone)
    lessons = await call_api(client_for(user).available_lessons(start, end, teacher_id))
    note = None if lessons else "No open lessons in this window."
    return lessons_json(lessons, timezone, note=note)
