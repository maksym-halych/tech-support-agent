"""Bounds on how the model calls tools within one turn."""

from typing import Any

from langchain.agents.middleware import ToolCallLimitMiddleware
from langchain_core.messages import ToolMessage
from langgraph.runtime import Runtime

from tech_support_agent.prompts import MAX_TOOL_CALLS_PER_TURN

TOOL_CALL_LIMIT_MESSAGE = (
    "Tool call limit reached: this turn has already made "
    f"{MAX_TOOL_CALLS_PER_TURN} tool calls, so this one was not run. Do not call "
    "tools again; answer with what you have."
)


class _ToolCallLimitMiddleware(ToolCallLimitMiddleware):
    """Cap the tool calls in one run, worded so the model answers instead of retrying."""

    @staticmethod
    def _reword(update: dict[str, Any] | None) -> dict[str, Any] | None:
        """
        Replace the stock refusal text on blocked tool calls with ours.

        Args:
            update: The state update the stock middleware produced.

        Returns:
            The same update, its refusal messages reworded.
        """

        if not update or "messages" not in update:
            return update

        update["messages"] = [
            message.model_copy(update={"content": TOOL_CALL_LIMIT_MESSAGE})
            if isinstance(message, ToolMessage)
            else message
            for message in update["messages"]
        ]
        return update

    def after_model(self, state: Any, runtime: Runtime[Any]) -> dict[str, Any] | None:
        """
        Count this model call's tool calls and refuse those over the limit.

        Args:
            state: The agent state.
            runtime: The run's runtime.

        Returns:
            The counter update, plus a refusal for each blocked call.
        """

        return self._reword(super().after_model(state, runtime))

    async def aafter_model(
        self, state: Any, runtime: Runtime[Any]
    ) -> dict[str, Any] | None:
        """
        Count this model call's tool calls and refuse those over the limit.

        Args:
            state: The agent state.
            runtime: The run's runtime.

        Returns:
            The counter update, plus a refusal for each blocked call.
        """

        return self._reword(await super().aafter_model(state, runtime))


def tool_call_limit(
    run_limit: int = MAX_TOOL_CALLS_PER_TURN,
) -> ToolCallLimitMiddleware:
    """
    Build the middleware capping tool calls per turn, across all tools.

    Args:
        run_limit: Tool calls allowed in one run.

    Returns:
        The middleware.
    """

    return _ToolCallLimitMiddleware(run_limit=run_limit, exit_behavior="continue")
