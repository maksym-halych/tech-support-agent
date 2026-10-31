"""Tool: cancel a booking or a lesson. Needs the user's approval."""

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


class CancelLessonInput(BaseModel):
    """Input for cancel_lesson."""

    lesson_id: str = Field(
        min_length=1, description="ID of one of the user's lessons from list_my_lessons"
    )


@lesson_tool(CancelLessonInput)
async def cancel_lesson(runtime: Runtime, lesson_id: str) -> str:
    """
    Cancel a lesson. A student cancels their booking and the lesson reopens for others; a teacher cancels the lesson itself. The user approves the cancellation before it happens.

    Returns:
        JSON with the lesson after cancellation.
    """

    user = require_user(runtime)
    lesson = await call_api(client_for(user).cancel_lesson(lesson_id))
    outcome = "booking cancelled" if user.role == "student" else "lesson cancelled"
    return lesson_json(lesson, user_timezone(runtime), outcome)
