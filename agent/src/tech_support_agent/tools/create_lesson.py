"""Tool: open a new lesson slot as the teacher. Needs the user's approval."""

from datetime import datetime

from pydantic import BaseModel, Field

from tech_support_agent.lessons.client import client_for
from tech_support_agent.tools.shared import (
    Runtime,
    call_api,
    lesson_json,
    lesson_tool,
    local_datetime,
    require_user,
    user_timezone,
)


class CreateLessonInput(BaseModel):
    """Input for create_lesson."""

    subject: str = Field(
        min_length=1, max_length=100, description="What the lesson is about"
    )
    starts_at: datetime = Field(
        description="Local start time in the user's timezone, YYYY-MM-DDTHH:MM"
    )
    duration_minutes: int = Field(
        default=60, ge=15, le=240, description="Length in minutes, 15 to 240"
    )


@lesson_tool(CreateLessonInput)
async def create_lesson(
    runtime: Runtime, subject: str, starts_at: datetime, duration_minutes: int = 60
) -> str:
    """
    Open a new lesson slot that students can book. The teacher approves it before it is created.

    Returns:
        JSON with the new open lesson.
    """

    user = require_user(runtime, "teacher")
    timezone = user_timezone(runtime)
    lesson = await call_api(
        client_for(user).create_lesson(
            subject.strip(), local_datetime(starts_at, timezone), duration_minutes
        )
    )
    return lesson_json(lesson, timezone, "created")
