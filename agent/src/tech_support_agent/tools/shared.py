"""Helpers shared by the lesson tools: the caller, their timezone, API errors."""

from collections.abc import Awaitable, Callable
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from langchain.tools import ToolRuntime
from langchain_core.tools import BaseTool, ToolException, tool
from pydantic import BaseModel

from tech_support_agent.context import AgentContext, SignedInUser, signed_in_user
from tech_support_agent.lessons.client import LessonsApiError
from tech_support_agent.lessons.models import Lesson, LessonView

Runtime = ToolRuntime[AgentContext]

NOT_SIGNED_IN = "Nobody is signed in, so lessons cannot be read or changed."


def lesson_tool(
    args_schema: type[BaseModel] | None = None,
) -> Callable[[Callable[..., Awaitable[str]]], BaseTool]:
    """
    Turn an async function into a tool whose errors go back to the model.

    Args:
        args_schema: The tool's input model; None infers it from a function
            without model-supplied parameters (an empty explicit schema would
            stop LangChain from injecting ``runtime``).

    Returns:
        A decorator producing the tool.
    """

    def decorate(function: Callable[..., Awaitable[str]]) -> BaseTool:
        built = (
            tool(function, args_schema=args_schema)
            if args_schema is not None
            else tool(function)
        )
        # A ToolException becomes an error ToolMessage the model can explain.
        built.handle_tool_error = True
        return built

    return decorate


def require_user(runtime: Runtime, role: str | None = None) -> SignedInUser:
    """
    Return the signed-in user, optionally requiring a role.

    Args:
        runtime: The tool runtime.
        role: ``student`` or ``teacher`` if the tool is for one role only.

    Returns:
        The user.

    Raises:
        ToolException: If nobody is signed in or the user has another role.
    """

    user = signed_in_user(runtime.server_info)
    if user is None:
        raise ToolException(NOT_SIGNED_IN)
    if role is not None and user.role != role:
        raise ToolException(f"This is only available to {role}s.")
    return user


def user_timezone(runtime: Runtime) -> ZoneInfo:
    """
    Return the timezone the frontend sent with this run.

    Args:
        runtime: The tool runtime.

    Returns:
        The user's timezone.
    """

    return ZoneInfo((runtime.context or AgentContext()).timezone)


def local_datetime(value: datetime, timezone: ZoneInfo) -> datetime:
    """
    Read a time without an offset as local time in the user's timezone.

    Args:
        value: A naive or aware datetime.
        timezone: The user's timezone.

    Returns:
        A timezone-aware datetime.
    """

    return value if value.tzinfo is not None else value.replace(tzinfo=timezone)


def day_window(
    date_from: date, date_to: date | None, timezone: ZoneInfo
) -> tuple[datetime, datetime]:
    """
    Turn local calendar days into an aware time window.

    Args:
        date_from: First day, inclusive.
        date_to: Last day, inclusive; defaults to ``date_from``.
        timezone: The user's timezone.

    Returns:
        Start of ``date_from`` and start of the day after ``date_to``.
    """

    last = date_to or date_from
    start = datetime.combine(date_from, time.min, timezone)
    end = datetime.combine(last + timedelta(days=1), time.min, timezone)
    return start, end


async def call_api[T](request: Awaitable[T]) -> T:
    """
    Await a lessons API call, turning its errors into tool errors.

    Args:
        request: The pending client call.

    Returns:
        The call's result.

    Raises:
        ToolException: With a message the model can pass on to the user.
    """

    try:
        return await request
    except LessonsApiError as error:
        raise ToolException(error.message) from None


def lessons_json(lessons: list[Lesson], timezone: ZoneInfo, **extra: Any) -> str:
    """
    Serialize lessons for the model, with local times.

    Args:
        lessons: Lessons from the API.
        timezone: The user's timezone.
        **extra: Additional top-level fields.

    Returns:
        Compact JSON.
    """

    return _Lessons(
        timezone=str(timezone),
        lessons=[LessonView.of(lesson, timezone) for lesson in lessons],
        **extra,
    ).model_dump_json(exclude_none=True)


def lesson_json(lesson: Lesson, timezone: ZoneInfo, outcome: str) -> str:
    """
    Serialize the result of a write for the model.

    Args:
        lesson: The lesson after the write.
        timezone: The user's timezone.
        outcome: What happened, e.g. ``booked``.

    Returns:
        Compact JSON.
    """

    return _Outcome(
        outcome=outcome, timezone=str(timezone), lesson=LessonView.of(lesson, timezone)
    ).model_dump_json()


class _Lessons(BaseModel):
    timezone: str
    lessons: list[LessonView]
    note: str | None = None


class _Outcome(BaseModel):
    outcome: str
    timezone: str
    lesson: LessonView
