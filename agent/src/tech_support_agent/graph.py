"""Reusable LangGraph subgraph with deterministic validation and write approval."""

import hashlib
import json
from typing import Annotated, Any, Literal, TypedDict
from uuid import uuid4

import httpx
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AnyMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph, add_messages
from langgraph.graph.state import CompiledStateGraph
from langgraph.runtime import Runtime
from langgraph.types import interrupt
from pydantic import ValidationError

from tech_support_agent.api import ApiExecutor, redact, redact_json
from tech_support_agent.catalog import CatalogError, OpenApiCatalog
from tech_support_agent.config import Settings
from tech_support_agent.journal import ActionJournal
from tech_support_agent.models import (
    ApiConfig,
    ApiRequest,
    ApiResponse,
    ApprovalDecision,
    PendingAction,
    SupportContext,
    SupportResult,
)
from tech_support_agent.prompts import MAX_TOOL_CALLS_PER_TURN, SYSTEM_PROMPT
from tech_support_agent.tools.get_current_datetime import get_current_datetime
from tech_support_agent.tools.registry import (
    TOOL_MODELS,
    TOOLS,
    CallOperation,
    Finish,
    GetOperation,
)


class SupportInput(TypedDict):
    """Only messages are writable by callers; internal state stays private."""

    messages: Annotated[list[AnyMessage], add_messages]


class SupportOutput(TypedDict):
    """Conversation updates and typed orchestrator result."""

    messages: Annotated[list[AnyMessage], add_messages]
    result: SupportResult | None


class SupportState(SupportInput):
    """Checkpointed state, deliberately excluding runtime credentials."""

    owner_user_id: str
    integration_fingerprint: str
    tool_count: int
    queue: list[dict[str, Any]]
    pending: PendingAction | None
    result: SupportResult | None
    last_response: ApiResponse | None
    unknown_fingerprints: list[str]


def create_chat_model(settings: Settings) -> BaseChatModel:
    """Build the existing OpenAI-compatible model without changing providers."""

    if not settings.openai_api_key.get_secret_value():
        raise ValueError(
            "OPENAI_API_KEY is not set: configure agent/.env before starting the chat"
        )
    return ChatOpenAI(
        model=settings.agent_model,
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url,
        use_responses_api=False,
    )


def build_support_graph(
    model: BaseChatModel,
    api_config: ApiConfig,
    checkpointer: BaseCheckpointSaver | Literal[True] | None = None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    journal: ActionJournal | None = None,
) -> CompiledStateGraph:
    """Compile the async graph for standalone or parent-graph use.

    Args:
        model: Chat model supporting tool calls.
        api_config: Trusted origin and local OpenAPI document.
        checkpointer: Standalone saver, or True for persistent parent-owned state.
            None inherits the parent's saver for one subgraph invocation.
        transport: Optional HTTP transport for tests.
        journal: Write receipts; provide a durable journal across restarts.

    Returns:
        Graph accepting messages and a trusted SupportContext at invocation.
    """

    catalog = OpenApiCatalog(api_config)
    executor = ApiExecutor(catalog, transport)
    receipts = journal if journal is not None else ActionJournal()
    tool_model = model.bind_tools(TOOLS)

    def guard(state: SupportState, runtime: Runtime[SupportContext]) -> SupportContext:
        context = runtime.context
        if not isinstance(context, SupportContext):
            raise ValueError("A trusted SupportContext is required")
        if state.get("owner_user_id") not in {None, context.user_id}:
            raise PermissionError("This conversation belongs to another user")
        if state.get("integration_fingerprint") not in {None, catalog.fingerprint}:
            raise ValueError("API configuration changed; start a new conversation")
        return context

    def start(state: SupportState, runtime: Runtime[SupportContext]) -> dict[str, Any]:
        context = guard(state, runtime)
        if state.get("pending") is not None:
            raise ValueError(
                "Resolve the pending approval before sending a new message"
            )
        return {
            "owner_user_id": context.user_id,
            "integration_fingerprint": catalog.fingerprint,
            "tool_count": 0,
            "queue": [],
            "result": None,
            "last_response": None,
        }

    async def agent(
        state: SupportState, runtime: Runtime[SupportContext]
    ) -> dict[str, Any]:
        context = guard(state, runtime)
        try:
            answer = await tool_model.ainvoke(
                [
                    SystemMessage(
                        content=f"{SYSTEM_PROMPT}\nUser timezone: {context.timezone}."
                    ),
                    *state["messages"],
                ]
            )
        except Exception:
            result = SupportResult(
                status="failed",
                message="The language model is unavailable. Please try again later.",
            )
            return {"result": result, "messages": [AIMessage(content=result.message)]}
        if not isinstance(answer, AIMessage):
            raise TypeError("Expected an AIMessage from the chat model")
        answer = AIMessage.model_validate(
            redact_json(answer.model_dump(mode="json"), context)
        )
        if answer.tool_calls:
            return {"messages": [answer], "queue": list(answer.tool_calls)}
        return {
            "messages": [answer],
            "result": SupportResult(
                status="unknown_outcome"
                if state.get("unknown_fingerprints")
                else "needs_input",
                message=answer.text,
            ),
        }

    def tool_result(
        call: dict[str, Any], payload: Any, error: bool = False
    ) -> ToolMessage:
        return ToolMessage(
            content=json.dumps(payload, ensure_ascii=False),
            tool_call_id=call["id"],
            name=call["name"],
            status="error" if error else "success",
        )

    async def tools(
        state: SupportState, runtime: Runtime[SupportContext]
    ) -> dict[str, Any]:
        context = guard(state, runtime)
        call, *remaining = state["queue"]
        update: dict[str, Any] = {
            "queue": remaining,
            "tool_count": state.get("tool_count", 0) + 1,
        }
        if update["tool_count"] > MAX_TOOL_CALLS_PER_TURN:
            result = SupportResult(
                status="failed",
                message="Tool call limit reached. Please narrow the request or continue in a new turn.",
            )
            return {
                **update,
                "queue": [],
                "result": result,
                "messages": [
                    *(
                        tool_result(item, {"error": "Tool call limit reached"}, True)
                        for item in [call, *remaining]
                    ),
                    AIMessage(content=result.message),
                ],
            }
        try:
            schema = TOOL_MODELS.get(call["name"])
            if schema is None:
                raise CatalogError("Unknown tool")
            args = schema.model_validate(call["args"])
            if call["name"] == "list_operations":
                payload = [entry.model_dump() for entry in catalog.list_operations()]
            elif isinstance(args, GetOperation):
                payload = catalog.get_operation(args.operation_id).model_dump()
            elif call["name"] == "get_current_datetime":
                payload = get_current_datetime(context.timezone).model_dump()
            elif isinstance(args, Finish):
                result = SupportResult(
                    status=args.status,
                    message=args.message,
                    data=state["last_response"].data
                    if state.get("last_response")
                    else None,
                )
                if state.get("unknown_fingerprints"):
                    result = SupportResult(
                        status="unknown_outcome",
                        message="An earlier write may have succeeded. Check its outcome before making another attempt.",
                    )
                elif (
                    state.get("last_response")
                    and state["last_response"].outcome == "failed"
                    and args.status == "completed"
                ):
                    result = SupportResult(
                        status="failed",
                        message=state["last_response"].error
                        or "The API operation failed.",
                    )
                return {
                    **update,
                    "queue": [],
                    "result": result,
                    "messages": [
                        tool_result(call, {"status": result.status}),
                        *(
                            tool_result(item, {"error": "Skipped after finish"}, True)
                            for item in remaining
                        ),
                        AIMessage(content=result.message),
                    ],
                }
            elif isinstance(args, CallOperation):
                request = ApiRequest.model_validate(
                    args.model_dump(exclude={"confirmation_summary"})
                )
                operation = catalog.validate_request(request)
                executor.auth_headers(operation, context)
                fingerprint = hashlib.sha256(
                    f"{context.user_id}:{catalog.fingerprint}:{json.dumps(request.model_dump(), sort_keys=True)}".encode()
                ).hexdigest()
                if fingerprint in state.get("unknown_fingerprints", []):
                    raise CatalogError(
                        "This write has an unknown outcome; reconcile it instead of repeating it"
                    )
                if operation.method not in {"GET", "HEAD"}:
                    if not args.confirmation_summary.strip():
                        raise CatalogError(
                            "A write needs a specific human-readable confirmation_summary"
                        )
                    return {
                        **update,
                        "pending": PendingAction(
                            action_id=str(uuid4()),
                            request=request,
                            fingerprint=fingerprint,
                            summary=args.confirmation_summary,
                            tool_call_id=call["id"],
                            integration_fingerprint=catalog.fingerprint,
                        ),
                    }
                response = await executor.execute(request, context)
                update["last_response"] = response
                payload = response.model_dump()
            else:
                raise CatalogError("Unknown tool")
            update["messages"] = [tool_result(call, redact_json(payload, context))]
        except (CatalogError, ValidationError, ValueError) as exc:
            error = (
                "Invalid tool arguments"
                if isinstance(exc, ValidationError)
                else redact(str(exc), context)
            )
            update["messages"] = [tool_result(call, {"error": error}, True)]
            update["last_response"] = ApiResponse(outcome="failed", error=error)
        return update

    def approve(
        state: SupportState, runtime: Runtime[SupportContext]
    ) -> dict[str, Any]:
        guard(state, runtime)
        pending = state["pending"]
        if pending is None or pending.integration_fingerprint != catalog.fingerprint:
            raise ValueError("No matching pending action")
        error: str | None = None
        while True:
            raw = interrupt(
                {
                    "kind": "api_write_approval",
                    "action_id": pending.action_id,
                    "summary": pending.summary,
                    "request": pending.request.model_dump(),
                    "error": error,
                }
            )
            try:
                decision = ApprovalDecision.model_validate(raw)
                if decision.action_id != pending.action_id:
                    raise ValueError("Stale action ID")
                break
            except ValidationError, ValueError:
                error = "Approve or reject this exact action ID; parameters cannot be edited in an approval."
        if decision.decision == "reject":
            result = SupportResult(
                status="needs_input",
                message="Дію відхилено. Можна змінити вибір або зробити інший запит.",
            )
            return {
                "pending": None,
                "queue": [],
                "result": result,
                "messages": [
                    ToolMessage(
                        content='{"rejected": true}',
                        tool_call_id=pending.tool_call_id,
                        name="call_operation",
                    ),
                    *(
                        tool_result(item, {"error": "Skipped after rejection"}, True)
                        for item in state.get("queue", [])
                    ),
                    AIMessage(content=result.message),
                ],
            }
        return {}

    async def execute_write(
        state: SupportState, runtime: Runtime[SupportContext]
    ) -> dict[str, Any]:
        context = guard(state, runtime)
        pending = state["pending"]
        if pending is None:
            raise ValueError("No approved action")
        response = receipts.claim(pending.action_id, pending.fingerprint)
        if response is None:
            try:
                response = await executor.execute(
                    pending.request, context, pending.action_id
                )
            except CatalogError, ValueError:
                response = ApiResponse(
                    outcome="failed",
                    error="Approved request could not be validated or authorized",
                )
            receipts.complete(pending.action_id, response)
        message = ToolMessage(
            content=response.model_dump_json(),
            tool_call_id=pending.tool_call_id,
            name="call_operation",
            status="success" if response.outcome == "success" else "error",
        )
        update: dict[str, Any] = {
            "pending": None,
            "last_response": response,
            "messages": [message],
        }
        if response.outcome == "unknown_outcome":
            result = SupportResult(
                status="unknown_outcome",
                message="Результат дії невідомий: вона могла виконатися. Перевірте записи перед новою спробою.",
                data=response.model_dump(),
            )
            update.update(
                unknown_fingerprints=[
                    *state.get("unknown_fingerprints", []),
                    pending.fingerprint,
                ],
                queue=[],
                result=result,
            )
            update["messages"].extend(
                [
                    *(
                        tool_result(
                            item, {"error": "Skipped after unknown write outcome"}, True
                        )
                        for item in state.get("queue", [])
                    ),
                    AIMessage(content=result.message),
                ]
            )
        return update

    def route(state: SupportState) -> str:
        if state.get("result") is not None:
            return END
        if state.get("pending") is not None:
            return "approve"
        return "tools" if state.get("queue") else "agent"

    builder = StateGraph(
        SupportState,
        input_schema=SupportInput,
        output_schema=SupportOutput,
        context_schema=SupportContext,
    )
    for name, node in (
        ("start", start),
        ("agent", agent),
        ("tools", tools),
        ("approve", approve),
        ("execute_write", execute_write),
    ):
        builder.add_node(name, node)
    builder.add_edge(START, "start")
    builder.add_edge("start", "agent")
    for node in ("agent", "tools", "execute_write"):
        builder.add_conditional_edges(
            node,
            route,
            {END: END, "agent": "agent", "tools": "tools", "approve": "approve"},
        )
    builder.add_conditional_edges(
        "approve",
        lambda state: END if state.get("result") else "execute_write",
        {END: END, "execute_write": "execute_write"},
    )
    return builder.compile(checkpointer=checkpointer, name="support_agent").with_config(
        {"recursion_limit": 64}
    )
