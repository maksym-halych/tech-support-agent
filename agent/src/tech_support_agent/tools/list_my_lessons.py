"""Tool: the user's own lessons."""

from pydantic import BaseModel, Field

from tech_support_agent.lessons.client import client_for
from tech_support_agent.tools.shared import (
    Runtime,
    call_api,
    lesson_tool,
    lessons_json,
    require_user,
    user_timezone,
)


class ListMyLessonsInput(BaseModel):
    """Input for list_my_lessons."""

    include_past: bool = Field(
        default=False, description="Also include lessons that have already ended"
    )


@lesson_tool(ListMyLessonsInput)
async def list_my_lessons(runtime: Runtime, include_past: bool = False) -> str:
    """
    List the user's lessons: for a student the lessons they booked, for a teacher the lessons they teach in any status.

    Use it before cancelling, to find the lesson the user means.

    Returns:
        JSON with the lessons, times in the user's timezone.
    """

    user = require_user(runtime)
    lessons = await call_api(client_for(user).my_lessons(include_past))
    return lessons_json(lessons, user_timezone(runtime))
