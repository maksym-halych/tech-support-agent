"""System prompt that tells the model who the user is and what time it is."""

from datetime import datetime
from zoneinfo import ZoneInfo

from langchain.agents.middleware import ModelRequest, dynamic_prompt

from tech_support_agent.context import AgentContext, signed_in_user
from tech_support_agent.prompts import NOT_SIGNED_IN, SYSTEM_PROMPT, run_details


@dynamic_prompt
def lesson_prompt(request: ModelRequest) -> str:
    """
    Build the system prompt for one model call.

    Args:
        request: The model request; its runtime carries the run context and user.

    Returns:
        The base prompt plus the user's name, role, timezone and local time.
    """

    context = request.runtime.context or AgentContext()
    user = signed_in_user(request.runtime.server_info)
    if user is None:
        return f"{SYSTEM_PROMPT}\n{NOT_SIGNED_IN}\n"
    now = datetime.now(ZoneInfo(context.timezone))
    local_now = now.strftime("%A, %d %B %Y, %H:%M (UTC%z)")
    return (
        f"{SYSTEM_PROMPT}\n"
        f"{run_details(user.name, user.role, context.timezone, local_now)}"
    )
