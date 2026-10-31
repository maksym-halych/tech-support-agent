"""Tool: book an open lesson for the student. Needs the user's approval."""

from pydantic import BaseModel, Field

from tech_support_agent.lessons.client import client_for
from tech_support_agent.tools.shared import (
    Runtime,
    call_api,
    lesson_json,
    lesson_tool,
    require_user,
    user_timezone,
)


class BookLessonInput(BaseModel):
    """Input for book_lesson."""

    lesson_id: str = Field(
        min_length=1, description="ID of an open lesson from find_available_lessons"
    )


@lesson_tool(BookLessonInput)
async def book_lesson(runtime: Runtime, lesson_id: str) -> str:
    """
    Book an open lesson for the student, after they chose it. The user approves the booking before it happens.

    Returns:
        JSON with the booked lesson.
    """

    user = require_user(runtime, "student")
    lesson = await call_api(client_for(user).book_lesson(lesson_id))
    return lesson_json(lesson, user_timezone(runtime), "booked")
