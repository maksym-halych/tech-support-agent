"""Show the model only the tools the signed-in user's role can use."""

from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.tools import BaseTool

from tech_support_agent.context import signed_in_user
from tech_support_agent.tools.registry import tool_names_for


def _for_role(request: ModelRequest) -> ModelRequest:
    user = signed_in_user(request.runtime.server_info)
    allowed = tool_names_for(user.role if user else None)
    tools = [
        candidate
        for candidate in request.tools
        if not isinstance(candidate, BaseTool) or candidate.name in allowed
    ]
    return request.override(tools=tools)


class RoleToolsMiddleware(AgentMiddleware):
    """Students never see ``create_lesson``; teachers never see booking tools.

    The lessons API enforces roles anyway; this keeps the model from proposing
    actions the user cannot take.
    """

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        """
        Call the model with the role's tools only.

        Args:
            request: The model request.
            handler: Calls the model.

        Returns:
            The model's response.
        """

        return handler(_for_role(request))

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> Any:
        """
        Call the model with the role's tools only.

        Args:
            request: The model request.
            handler: Calls the model.

        Returns:
            The model's response.
        """

        return await handler(_for_role(request))
