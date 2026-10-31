"""Human approval before every booking, new lesson and cancellation.

Each write tool call pauses the run with a ``HITLRequest`` whose description
says in plain words what will happen. Resume the run with
``Command(resume={"decisions": [{"type": "approve"} | {"type": "reject", "message": "..."}]})``,
one decision per action request.
"""

import asyncio
import json
import logging
from contextvars import ContextVar
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from langchain.agents.middleware import HumanInTheLoopMiddleware, InterruptOnConfig
from langchain.agents.middleware.types import AgentState
from langchain_core.messages import AIMessage, ToolCall, ToolMessage
from langgraph.runtime import Runtime

from tech_support_agent.context import AgentContext, signed_in_user
from tech_support_agent.lessons.client import LessonsApiError, client_for
from tech_support_agent.lessons.models import Lesson, LessonView
from tech_support_agent.tools.registry import WRITE_TOOLS, tool_names_for
from tech_support_agent.tools.shared import local_datetime

logger = logging.getLogger(__name__)

# Lessons fetched for the pending tool calls of the current model turn.
_prefetched: ContextVar[dict[str, Lesson] | None] = ContextVar(
    "prefetched_lessons", default=None
)


def _timezone(runtime: Runtime[AgentContext]) -> ZoneInfo:
    return ZoneInfo((runtime.context or AgentContext()).timezone)


def _lesson_from_history(
    state: AgentState[Any], lesson_id: str
) -> dict[str, Any] | None:
    """Find a lesson the conversation already showed, newest tool result first."""

    for message in reversed(state["messages"]):
        if not isinstance(message, ToolMessage) or not isinstance(message.content, str):
            continue
        try:
            payload = json.loads(message.content)
        except ValueError:
            continue
        if not isinstance(payload, dict):
            continue
        candidates = payload.get("lessons") or [payload.get("lesson")]
        for candidate in candidates:
            if isinstance(candidate, dict) and candidate.get("id") == lesson_id:
                return candidate
    return None


def _lesson_view(
    lesson_id: str, state: AgentState[Any], runtime: Runtime[AgentContext]
) -> dict[str, Any] | None:
    lesson = (_prefetched.get() or {}).get(lesson_id)
    if lesson is not None:
        return LessonView.of(lesson, _timezone(runtime)).model_dump()
    return _lesson_from_history(state, lesson_id)


def _when(view: dict[str, Any], timezone: ZoneInfo) -> str:
    return f"{view['day']}, {view['time']} ({timezone})"


def describe_booking(
    tool_call: ToolCall, state: AgentState[Any], runtime: Runtime[AgentContext]
) -> str:
    """
    Describe a booking for the approval card.

    Args:
        tool_call: The pending ``book_lesson`` call.
        state: The agent state.
        runtime: The run's runtime.

    Returns:
        A one-line summary of the booking.
    """

    lesson_id = str(tool_call["args"].get("lesson_id", ""))
    view = _lesson_view(lesson_id, state, runtime)
    if view is None:
        return f"Book lesson {lesson_id}. Its details could not be loaded."
    return (
        f"Book {view['subject']} with {view['teacher']} on "
        f"{_when(view, _timezone(runtime))}, {view['duration_minutes']} min."
    )


def describe_new_lesson(
    tool_call: ToolCall, state: AgentState[Any], runtime: Runtime[AgentContext]
) -> str:
    """
    Describe a new lesson for the approval card.

    Args:
        tool_call: The pending ``create_lesson`` call.
        state: The agent state.
        runtime: The run's runtime.

    Returns:
        A one-line summary of the lesson to open.
    """

    args = tool_call["args"]
    timezone = _timezone(runtime)
    subject = str(args.get("subject", "")).strip()
    try:
        duration = int(args.get("duration_minutes", 60))
        start = local_datetime(datetime.fromisoformat(str(args["starts_at"])), timezone)
    except KeyError, TypeError, ValueError:
        return (
            f"Open a new lesson: {subject or 'no subject'} ({args.get('starts_at')})."
        )
    start = start.astimezone(timezone)
    end = start + timedelta(minutes=duration)
    return (
        f"Open a new {duration}-minute lesson: {subject} on "
        f"{start:%A, %d %B %Y}, {start:%H:%M}–{end:%H:%M} ({timezone})."
    )


def describe_cancellation(
    tool_call: ToolCall, state: AgentState[Any], runtime: Runtime[AgentContext]
) -> str:
    """
    Describe a cancellation for the approval card, worded for the user's role.

    Args:
        tool_call: The pending ``cancel_lesson`` call.
        state: The agent state.
        runtime: The run's runtime.

    Returns:
        A one- or two-sentence summary of what the cancellation does.
    """

    lesson_id = str(tool_call["args"].get("lesson_id", ""))
    view = _lesson_view(lesson_id, state, runtime)
    if view is None:
        return f"Cancel lesson {lesson_id}. Its details could not be loaded."
    user = signed_in_user(runtime.server_info)
    when = _when(view, _timezone(runtime))
    if user is not None and user.role == "teacher":
        student = view.get("student")
        audience = (
            f" It is booked by {student}, who will see it as cancelled."
            if student
            else " Nobody has booked it yet."
        )
        return f"Cancel your lesson {view['subject']} on {when}.{audience}"
    return (
        f"Cancel your booking of {view['subject']} with {view['teacher']} on "
        f"{when}. The lesson reopens for other students."
    )


def _allowed_for_role(request: Any) -> bool:
    """Ask for approval only for tools the user's role can use at all."""

    user = signed_in_user(request.runtime.server_info)
    return request.tool_call["name"] in tool_names_for(user.role if user else None)


DESCRIPTIONS = {
    "book_lesson": describe_booking,
    "create_lesson": describe_new_lesson,
    "cancel_lesson": describe_cancellation,
}


class LessonApprovalMiddleware(HumanInTheLoopMiddleware):
    """Pause before every write; fetch the affected lessons so the card names them."""

    def __init__(self) -> None:
        """Gate the write tools with approve/reject decisions."""

        super().__init__(
            interrupt_on={
                name: InterruptOnConfig(
                    allowed_decisions=["approve", "reject"],
                    description=DESCRIPTIONS[name],
                    when=_allowed_for_role,
                )
                for name in sorted(WRITE_TOOLS)
            }
        )

    async def aafter_model(
        self, state: AgentState[Any], runtime: Runtime[AgentContext]
    ) -> dict[str, Any] | None:
        """
        Fetch the lessons the pending writes refer to, then pause for approval.

        Args:
            state: The agent state.
            runtime: The run's runtime.

        Returns:
            The state update from the approval step.
        """

        token = _prefetched.set(await self._fetch_lessons(state, runtime))
        try:
            return self.after_model(state, runtime)
        finally:
            _prefetched.reset(token)

    @staticmethod
    async def _fetch_lessons(
        state: AgentState[Any], runtime: Runtime[AgentContext]
    ) -> dict[str, Lesson]:
        messages = state["messages"]
        last = messages[-1] if messages else None
        user = signed_in_user(runtime.server_info)
        if not isinstance(last, AIMessage) or user is None:
            return {}
        lesson_ids = {
            str(call["args"]["lesson_id"])
            for call in last.tool_calls
            if call["name"] in {"book_lesson", "cancel_lesson"}
            and call["args"].get("lesson_id")
        }
        if not lesson_ids:
            return {}
        client = client_for(user)
        results = await asyncio.gather(
            *(client.get_lesson(lesson_id) for lesson_id in lesson_ids),
            return_exceptions=True,
        )
        lessons: dict[str, Lesson] = {}
        for lesson_id, result in zip(lesson_ids, results, strict=True):
            if isinstance(result, Lesson):
                lessons[lesson_id] = result
            elif isinstance(result, LessonsApiError):
                logger.info("Cannot describe lesson %s: %s", lesson_id, result.message)
            else:
                raise result
        return lessons
