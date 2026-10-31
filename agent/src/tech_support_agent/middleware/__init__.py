"""Agent middleware: per-run prompt, role-based tools, tool call bounds and write approval."""

from tech_support_agent.middleware.approval import LessonApprovalMiddleware
from tech_support_agent.middleware.limits import (
    TOOL_CALL_LIMIT_MESSAGE,
    tool_call_limit,
)
from tech_support_agent.middleware.prompt import lesson_prompt
from tech_support_agent.middleware.roles import RoleToolsMiddleware

__all__ = [
    "TOOL_CALL_LIMIT_MESSAGE",
    "LessonApprovalMiddleware",
    "RoleToolsMiddleware",
    "lesson_prompt",
    "tool_call_limit",
]
