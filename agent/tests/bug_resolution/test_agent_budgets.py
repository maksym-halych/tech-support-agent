import asyncio

import pytest
from langchain_core.messages import AIMessage
from langchain_core.tools import tool

from tech_support_agent.bug_resolution.agents import (
    BudgetExceeded,
    StageFailure,
    StageLimits,
    execute_stage,
    text_input,
)
from tech_support_agent.bug_resolution.preflight import ProbeResult, check_model
from tests.conftest import scripted_model, tool_call_message


def final(value: object = 7, **extra: object) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": "submit_result",
                "args": {"value": value, **extra},
                "id": "final",
            }
        ],
    )


async def test_hard_tool_budget_prevents_entire_oversized_batch() -> None:
    calls = []

    @tool
    async def observe() -> str:
        """Record a harmless observation."""
        calls.append(1)
        return "ok"

    with pytest.raises(BudgetExceeded, match="Tool-call"):
        await execute_stage(
            scripted_model(tool_call_message("observe", 3)),
            "test",
            [],
            [observe],
            ProbeResult,
            StageLimits(tool_calls=2),
        )
    assert calls == []


async def test_turn_budget_stops_repeated_tool_requests() -> None:
    @tool
    async def observe() -> str:
        """Make a harmless observation."""
        return "ok"

    model = scripted_model(*(tool_call_message("observe", 1) for _ in range(3)))
    with pytest.raises(BudgetExceeded, match="Model-turn"):
        await execute_stage(
            model, "test", [], [observe], ProbeResult, StageLimits(model_turns=2)
        )


async def test_wall_clock_cancels_tool_and_model_await(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cancelled = asyncio.Event()

    @tool
    async def wait_forever() -> str:
        """Wait until cancelled by the hard budget."""
        try:
            await asyncio.sleep(100)
        finally:
            cancelled.set()
        return "never"

    with pytest.raises(BudgetExceeded, match="wall-clock"):
        await execute_stage(
            scripted_model(tool_call_message("wait_forever", 1)),
            "test",
            [],
            [wait_forever],
            ProbeResult,
            StageLimits(seconds=0.02),
        )
    assert cancelled.is_set()
    model = scripted_model(final())

    async def slow(*args: object, **kwargs: object) -> AIMessage:
        await asyncio.sleep(100)
        return final()

    monkeypatch.setattr(type(model), "ainvoke", slow)
    with pytest.raises(BudgetExceeded, match="wall-clock"):
        await execute_stage(
            model, "test", [], [], ProbeResult, StageLimits(seconds=0.02)
        )


@pytest.mark.parametrize(
    "message",
    [
        final("wrong"),
        final(7, approved_by=123),
        AIMessage(content="success"),
        tool_call_message("apply_patch", 1),
        AIMessage(
            content="",
            tool_calls=[
                {"name": "submit_result", "args": {"value": 7}, "id": "a"},
                {"name": "write", "args": {}, "id": "b"},
            ],
        ),
    ],
)
async def test_invalid_output_and_unauthorized_tools_fail_closed(
    message: AIMessage,
) -> None:
    with pytest.raises(StageFailure):
        await execute_stage(
            scripted_model(message),
            "test",
            text_input("input"),
            [],
            ProbeResult,
            StageLimits(),
        )


async def test_success_receipts_and_fresh_context() -> None:
    run = await execute_stage(
        scripted_model(final()), "test", [], [], ProbeResult, StageLimits()
    )
    assert run.output.value == 7 and run.model_turns == 1 and not run.artifacts
    await check_model(scripted_model(tool_call_message("probe_value", 1), final(731)))
    with pytest.raises(RuntimeError):
        await check_model(scripted_model(final(731)))
