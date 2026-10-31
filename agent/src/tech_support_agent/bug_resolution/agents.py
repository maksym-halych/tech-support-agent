"""Bounded, injected model executor shared by independent private stages."""

import asyncio
from collections.abc import Callable

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.tools import BaseTool
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field, ValidationError

from tech_support_agent.bug_resolution.adapters.sandbox import (
    ImageArtifact,
    SandboxPolicyError,
)
from tech_support_agent.bug_resolution.contracts import Contract
from tech_support_agent.config import InvestigationSettings


class StageFailure(RuntimeError):
    """A bounded stage failed; callers must report blocked/failed, never success."""


class BudgetExceeded(StageFailure):
    """Hard model-turn, tool-call, or elapsed-time budget exhausted."""


class StageLimits(Contract):
    """Explicit hard limits, independent of LangChain's continue middleware."""

    model_turns: int = Field(default=20, gt=0, le=100)
    tool_calls: int = Field(default=30, ge=0, le=100)
    seconds: float = Field(default=300, gt=0, le=600)


class StageRun[T: BaseModel](Contract):
    """Validated model result together with executor-owned tool receipts."""

    output: T
    artifacts: list[ImageArtifact] = Field(default_factory=list)
    tool_names: list[str] = Field(default_factory=list)
    model_turns: int


def create_bug_model(settings: InvestigationSettings) -> BaseChatModel:
    """Create exactly the configured OpenRouter model, with no fallback or tracing."""

    return ChatOpenAI(
        model=settings.model,
        api_key=settings.model_api_key.get_secret_value(),
        base_url=settings.model_base_url,
        use_responses_api=False,
        max_retries=0,
        timeout=settings.stage_seconds,
    )


async def execute_stage[T: BaseModel](
    model: BaseChatModel,
    prompt: str,
    messages: list[BaseMessage],
    tools: list[BaseTool],
    output_schema: type[T],
    limits: StageLimits,
    on_artifact: Callable[[ImageArtifact], None] | None = None,
) -> StageRun[T]:
    """Run one fresh tool loop with strict output and pre-dispatch hard budgets.

    Args:
        model: Injected model; no hidden provider fallback.
        prompt: Stage-specific system instructions.
        messages: Fresh input context, not a previous agent's reasoning.
        tools: Explicit stage capabilities.
        output_schema: Only these final fields may be returned by the model.
        limits: Invocation limits including all model and tool awaits.
        on_artifact: Trusted sink for actual screenshot receipts.
    Returns:
        Structured output and the actual tool receipts used to obtain it.
    """

    registry = {item.name: item for item in tools}
    if len(registry) != len(tools) or "submit_result" in registry:
        raise ValueError("Duplicate or reserved tool name")
    final_tool = {
        "type": "function",
        "function": {
            "name": "submit_result",
            "description": "Submit the final validated stage result.",
            "parameters": output_schema.model_json_schema(),
        },
    }
    bound = model.bind_tools([*tools, final_tool])
    history = [SystemMessage(content=prompt), *messages]
    called: list[str] = []
    artifacts: list[ImageArtifact] = []
    try:
        async with asyncio.timeout(limits.seconds):
            for turn in range(1, limits.model_turns + 1):
                response = await bound.ainvoke(history, config={"callbacks": []})
                if not isinstance(response, AIMessage) or response.invalid_tool_calls:
                    raise StageFailure("Invalid model tool response")
                history.append(response)
                calls = response.tool_calls
                if not calls:
                    raise StageFailure("Model did not return a structured result")
                if any(call["name"] == "submit_result" for call in calls):
                    if len(calls) != 1:
                        raise StageFailure(
                            "Final result cannot accompany tool operations"
                        )
                    output = output_schema.model_validate(calls[0]["args"])
                    return StageRun[output_schema](
                        output=output,
                        artifacts=artifacts,
                        tool_names=called,
                        model_turns=turn,
                    )
                if len(called) + len(calls) > limits.tool_calls:
                    raise BudgetExceeded("Tool-call budget exhausted")
                if any(call["name"] not in registry for call in calls):
                    raise StageFailure("Unknown or unauthorized tool")
                for call in calls:
                    called.append(call["name"])
                    try:
                        result = await registry[call["name"]].ainvoke(call["args"])
                    except SandboxPolicyError:
                        result = '{"error":"policy_or_stale_reference; inspect again"}'
                    else:
                        if call["name"] == "screenshot":
                            artifact = ImageArtifact.model_validate_json(result)
                            artifacts.append(artifact)
                            if on_artifact:
                                on_artifact(artifact)
                    history.append(ToolMessage(content=result, tool_call_id=call["id"]))
            raise BudgetExceeded("Model-turn budget exhausted")
    except TimeoutError:
        raise BudgetExceeded("Stage wall-clock budget exhausted") from None
    except ValidationError:
        raise StageFailure("Invalid structured model output") from None


def text_input(text: str) -> list[BaseMessage]:
    """Construct a fresh stage context for a trusted serialized input."""

    return [HumanMessage(content=text)]
