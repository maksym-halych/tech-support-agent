"""Model-facing schemas; graph nodes own execution and approval gates."""

from typing import Literal

from pydantic import Field

from tech_support_agent.models import ApiRequest, BoundaryModel


class ListOperations(BoundaryModel):
    """List available API operations and their summaries."""


class GetOperation(BoundaryModel):
    """Read resolved parameters, request schema, and response schema."""

    operation_id: str


class CallOperation(ApiRequest):
    """Call an API operation; writes require separate human approval."""

    confirmation_summary: str = Field(default="", max_length=2000)


class GetCurrentDatetime(BoundaryModel):
    """Get the current date and time in the trusted user's timezone."""


class Finish(BoundaryModel):
    """Return a user-facing reply and structured outcome to the caller."""

    status: Literal["completed", "needs_input", "unsupported", "failed"]
    message: str = Field(min_length=1)


TOOL_MODELS = {
    "list_operations": ListOperations,
    "get_operation": GetOperation,
    "call_operation": CallOperation,
    "get_current_datetime": GetCurrentDatetime,
    "finish": Finish,
}
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": name,
            "description": model.__doc__,
            "parameters": model.model_json_schema(),
        },
    }
    for name, model in TOOL_MODELS.items()
]
