"""Tool: teachers a student can book with."""

from pydantic import BaseModel

from tech_support_agent.lessons.client import client_for
from tech_support_agent.tools.shared import Runtime, call_api, lesson_tool, require_user


class _Teachers(BaseModel):
    teachers: list[dict[str, str]]


@lesson_tool()
async def list_teachers(runtime: Runtime) -> str:
    """
    List the teachers, with their IDs, so a student can search one teacher's lessons.

    Returns:
        JSON with each teacher's id and name.
    """

    user = require_user(runtime, "student")
    teachers = await call_api(client_for(user).teachers())
    return _Teachers(
        teachers=[{"id": teacher.id, "name": teacher.name} for teacher in teachers]
    ).model_dump_json()
