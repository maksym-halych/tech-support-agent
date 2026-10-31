"""The tool set handed to the agent, and which role may use which tool.

Kept out of ``tools/__init__.py`` on purpose, so importing a shared helper from
under ``tech_support_agent.tools`` does not pull in every tool module with it.
"""

from langchain_core.tools import BaseTool

from tech_support_agent.tools.book_lesson import book_lesson
from tech_support_agent.tools.cancel_lesson import cancel_lesson
from tech_support_agent.tools.create_lesson import create_lesson
from tech_support_agent.tools.find_available_lessons import find_available_lessons
from tech_support_agent.tools.get_current_datetime import get_current_datetime
from tech_support_agent.tools.list_my_lessons import list_my_lessons
from tech_support_agent.tools.list_teachers import list_teachers

TOOLS: list[BaseTool] = [
    get_current_datetime,
    list_my_lessons,
    list_teachers,
    find_available_lessons,
    book_lesson,
    create_lesson,
    cancel_lesson,
]

# Tools that change lessons; each needs the user's approval.
WRITE_TOOLS = frozenset({"book_lesson", "create_lesson", "cancel_lesson"})

_ANYONE = frozenset({"get_current_datetime"})
_SIGNED_IN = _ANYONE | {"list_my_lessons", "cancel_lesson"}
ROLE_TOOLS: dict[str | None, frozenset[str]] = {
    None: _ANYONE,
    "student": _SIGNED_IN | {"list_teachers", "find_available_lessons", "book_lesson"},
    "teacher": _SIGNED_IN | {"create_lesson"},
}


def tool_names_for(role: str | None) -> frozenset[str]:
    """
    Name the tools a role may use; the lessons API enforces the same split.

    Args:
        role: ``student``, ``teacher``, or None when nobody is signed in.

    Returns:
        The allowed tool names.
    """

    return ROLE_TOOLS.get(role, _ANYONE)


__all__ = [
    "TOOLS",
    "WRITE_TOOLS",
    "tool_names_for",
]
