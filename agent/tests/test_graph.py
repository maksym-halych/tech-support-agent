import json
from pathlib import Path

import httpx
import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from tech_support_agent.config import Settings
from tech_support_agent.graph import (
    SupportState,
    build_support_graph,
    create_chat_model,
)
from tech_support_agent.journal import ActionJournal
from tech_support_agent.models import ApiConfig, SupportContext
from tests.conftest import booking, call, finish, scripted_model

RUN = {"configurable": {"thread_id": "alice-thread"}, "recursion_limit": 100}


def approve(result: dict, decision: str = "approve") -> Command:
    return Command(
        resume={
            "action_id": result["__interrupt__"][0].value["action_id"],
            "decision": decision,
        }
    )


def test_preserves_provider_configuration() -> None:
    model = create_chat_model(Settings(_env_file=None))
    assert model.model_name == "gpt-6.1-sol"
    assert model.openai_api_base == "https://api.openai.com/v1"
    assert model.use_responses_api is False


async def test_booking_dialogue_requires_separate_approval(
    api_config: ApiConfig, context: SupportContext, transport: httpx.ASGITransport
) -> None:
    model = scripted_model(
        call("list_operations"),
        call("get_operation", operation_id="list_available_slots"),
        call(
            "call_operation",
            operation_id="list_available_slots",
            query_parameters={"date": "2026-11-03"},
        ),
        finish("О 14:00 доступні Ірина й Олег. Кого обираєте?", "needs_input"),
        booking(),
        finish("Запис підтверджено"),
    )
    graph = build_support_graph(model, api_config, InMemorySaver(), transport=transport)
    offered = await graph.ainvoke(
        {"messages": [HumanMessage("Запиши на наступний вівторок о 14:00")]},
        RUN,
        context=context,
    )
    assert offered["result"].status == "needs_input"
    pending = await graph.ainvoke(
        {"messages": [HumanMessage("До Ірини")]}, RUN, context=context
    )
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://demo.test",
        headers={"Authorization": "Bearer demo-alice"},
    ) as client:
        assert (await client.get("/me/bookings")).json() == []
        completed = await graph.ainvoke(approve(pending), RUN, context=context)
        records = (await client.get("/me/bookings")).json()
        assert len(records) == 1
        assert records[0]["slot"]["teacher_id"] == "iryna"
        assert completed["result"].status == "completed"
        assert completed["result"].data["id"] == records[0]["id"]
        await graph.ainvoke(approve(pending), RUN, context=context)
        assert len((await client.get("/me/bookings")).json()) == 1


async def test_rejection_and_new_choice_require_new_approval(
    api_config: ApiConfig, context: SupportContext, transport: httpx.ASGITransport
) -> None:
    graph = build_support_graph(
        scripted_model(booking(), booking("oleh:2026-11-03:14"), finish()),
        api_config,
        InMemorySaver(),
        transport=transport,
    )
    pending = await graph.ainvoke(
        {"messages": [HumanMessage("До Ірини")]}, RUN, context=context
    )
    rejected = await graph.ainvoke(approve(pending, "reject"), RUN, context=context)
    assert rejected["result"].status == "needs_input"
    replacement = await graph.ainvoke(
        {"messages": [HumanMessage("Тоді до Олега")]}, RUN, context=context
    )
    assert (
        replacement["__interrupt__"][0].value["action_id"]
        != pending["__interrupt__"][0].value["action_id"]
    )
    stale = await graph.ainvoke(approve(pending), RUN, context=context)
    assert stale["__interrupt__"][0].value["error"]
    result = await graph.ainvoke(approve(replacement), RUN, context=context)
    assert result["result"].data["slot"]["teacher_id"] == "oleh"


async def test_approval_cannot_edit_request(
    api_config: ApiConfig, context: SupportContext, transport: httpx.ASGITransport
) -> None:
    graph = build_support_graph(
        scripted_model(booking()), api_config, InMemorySaver(), transport=transport
    )
    pending = await graph.ainvoke(
        {"messages": [HumanMessage("Book")]}, RUN, context=context
    )
    response = await graph.ainvoke(
        Command(
            resume={
                "action_id": pending["__interrupt__"][0].value["action_id"],
                "decision": "approve",
                "body": {"slot_id": "other"},
            }
        ),
        RUN,
        context=context,
    )
    assert response["__interrupt__"][0].value["error"]


async def test_other_user_cannot_resume(
    api_config: ApiConfig, context: SupportContext, transport: httpx.ASGITransport
) -> None:
    graph = build_support_graph(
        scripted_model(booking()), api_config, InMemorySaver(), transport=transport
    )
    pending = await graph.ainvoke(
        {"messages": [HumanMessage("Book")]}, RUN, context=context
    )
    with pytest.raises(PermissionError):
        await graph.ainvoke(
            approve(pending), RUN, context=SupportContext(user_id="bob")
        )


async def test_sqlite_restore_has_no_credentials(
    tmp_path: Path,
    api_config: ApiConfig,
    context: SupportContext,
    transport: httpx.ASGITransport,
) -> None:
    checkpoint_file = tmp_path / "checkpoint.sqlite"
    journal = ActionJournal(tmp_path / "journal.sqlite")
    try:
        async with AsyncSqliteSaver.from_conn_string(str(checkpoint_file)) as saver:
            graph = build_support_graph(
                scripted_model(booking()),
                api_config,
                saver,
                transport=transport,
                journal=journal,
            )
            pending = await graph.ainvoke(
                {"messages": [HumanMessage("Book")]}, RUN, context=context
            )
        async with AsyncSqliteSaver.from_conn_string(str(checkpoint_file)) as saver:
            restored = build_support_graph(
                scripted_model(finish()),
                api_config,
                saver,
                transport=transport,
                journal=journal,
            )
            result = await restored.ainvoke(approve(pending), RUN, context=context)
            assert result["result"].status == "completed"
        assert b"demo-alice" not in checkpoint_file.read_bytes()
        assert b"demo-alice" not in (tmp_path / "journal.sqlite").read_bytes()
    finally:
        journal.close()


async def test_unknown_write_is_not_retried(
    api_config: ApiConfig, context: SupportContext
) -> None:
    seen = []

    def timeout(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        raise httpx.ReadTimeout("Disconnected after dispatch", request=request)

    graph = build_support_graph(
        scripted_model(booking(), booking(), finish()),
        api_config,
        InMemorySaver(),
        transport=httpx.MockTransport(timeout),
    )
    pending = await graph.ainvoke(
        {"messages": [HumanMessage("Book")]}, RUN, context=context
    )
    result = await graph.ainvoke(approve(pending), RUN, context=context)
    assert result["result"].status == "unknown_outcome"
    repeated = await graph.ainvoke(
        {"messages": [HumanMessage("Try again")]}, RUN, context=context
    )
    assert repeated["result"].status == "unknown_outcome"
    assert len(seen) == 1


async def test_parent_graph_inherits_checkpoint_and_context(
    api_config: ApiConfig, context: SupportContext, transport: httpx.ASGITransport
) -> None:
    support = build_support_graph(
        scripted_model(booking(), finish()),
        api_config,
        checkpointer=True,
        transport=transport,
    )
    parent = StateGraph(SupportState, context_schema=SupportContext)
    parent.add_node("support", support)
    parent.add_edge(START, "support")
    parent.add_edge("support", END)
    graph = parent.compile(checkpointer=InMemorySaver())
    pending = await graph.ainvoke(
        {"messages": [HumanMessage("Book")]}, RUN, context=context
    )
    result = await graph.ainvoke(approve(pending), RUN, context=context)
    assert result["result"].status == "completed"


@pytest.mark.parametrize(
    "status,message",
    [
        ("needs_input", "На яку дату?"),
        ("unsupported", "Для виправлення коду потрібен інший агент."),
    ],
)
async def test_orchestrator_outcomes(
    api_config: ApiConfig, context: SupportContext, status: str, message: str
) -> None:
    graph = build_support_graph(scripted_model(finish(message, status)), api_config)
    result = await graph.ainvoke({"messages": [HumanMessage("Help")]}, context=context)
    assert result["result"].status == status


async def test_tool_limit_and_sequential_batch(
    api_config: ApiConfig, context: SupportContext
) -> None:
    calls = [
        {
            "id": f"call-{i}",
            "name": "get_current_datetime",
            "args": {},
            "type": "tool_call",
        }
        for i in range(12)
    ]
    graph = build_support_graph(
        scripted_model(AIMessage(content="", tool_calls=calls)), api_config
    )
    result = await graph.ainvoke(
        {"messages": [HumanMessage("Time?")]}, {"recursion_limit": 100}, context=context
    )
    responses = [
        message for message in result["messages"] if isinstance(message, ToolMessage)
    ]
    assert len(responses) == 12
    assert sum(message.status == "error" for message in responses) == 2
    assert result["result"].status == "failed"


async def test_inventory_contract_works_without_business_code(
    tmp_path: Path, context: SupportContext
) -> None:
    spec = {
        "openapi": "3.1.0",
        "info": {"title": "Inventory", "version": "1"},
        "paths": {
            "/inventory/{sku}": {
                "get": {
                    "operationId": "stock",
                    "parameters": [
                        {
                            "name": "sku",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        }
                    ],
                    "responses": {
                        "200": {
                            "description": "Stock",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "required": ["quantity"],
                                        "properties": {"quantity": {"type": "integer"}},
                                    }
                                }
                            },
                        }
                    },
                }
            }
        },
    }
    path = tmp_path / "inventory.json"
    path.write_text(json.dumps(spec))

    def stock(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://inventory.test/inventory/ABC"
        return httpx.Response(200, json={"quantity": 7})

    graph = build_support_graph(
        scripted_model(
            call("list_operations"),
            call("get_operation", operation_id="stock"),
            call(
                "call_operation", operation_id="stock", path_parameters={"sku": "ABC"}
            ),
            finish("Є 7 одиниць"),
        ),
        ApiConfig(openapi_path=path, base_url="https://inventory.test"),
        transport=httpx.MockTransport(stock),
    )
    result = await graph.ainvoke(
        {"messages": [HumanMessage("Скільки ABC на складі?")]}, context=context
    )
    assert result["result"].data == {"quantity": 7}


async def test_pending_action_rejects_new_input(
    api_config: ApiConfig, context: SupportContext, transport: httpx.ASGITransport
) -> None:
    graph = build_support_graph(
        scripted_model(booking()), api_config, InMemorySaver(), transport=transport
    )
    await graph.ainvoke({"messages": [HumanMessage("Book")]}, RUN, context=context)
    with pytest.raises(ValueError, match="pending approval"):
        await graph.ainvoke(
            {"messages": [HumanMessage("Change the date")]}, RUN, context=context
        )


async def test_failed_write_cannot_be_reported_completed(
    api_config: ApiConfig, context: SupportContext
) -> None:
    graph = build_support_graph(
        scripted_model(booking(), finish("Booked!")),
        api_config,
        InMemorySaver(),
        transport=httpx.MockTransport(lambda request: httpx.Response(409)),
    )
    pending = await graph.ainvoke(
        {"messages": [HumanMessage("Book")]}, RUN, context=context
    )
    result = await graph.ainvoke(approve(pending), RUN, context=context)
    assert result["result"].status == "failed"
    assert "Booked!" not in result["result"].message


async def test_journal_fences_interrupted_dispatch_in_graph(
    tmp_path: Path, api_config: ApiConfig, context: SupportContext
) -> None:
    def forbidden(request: httpx.Request) -> httpx.Response:
        pytest.fail("A previously claimed write must not send HTTP again")

    journal = ActionJournal(tmp_path / "receipts.sqlite")
    try:
        graph = build_support_graph(
            scripted_model(booking()),
            api_config,
            InMemorySaver(),
            transport=httpx.MockTransport(forbidden),
            journal=journal,
        )
        pending = await graph.ainvoke(
            {"messages": [HumanMessage("Book")]}, RUN, context=context
        )
        state = await graph.aget_state(RUN)
        action = state.values["pending"]
        assert journal.claim(action.action_id, action.fingerprint) is None
        result = await graph.ainvoke(approve(pending), RUN, context=context)
        assert result["result"].status == "unknown_outcome"
    finally:
        journal.close()


async def test_sequential_tools_reach_budget_before_recursion_limit(
    api_config: ApiConfig, context: SupportContext
) -> None:
    graph = build_support_graph(
        scripted_model(*(call("get_current_datetime") for _ in range(11))), api_config
    )
    result = await graph.ainvoke({"messages": [HumanMessage("Time?")]}, context=context)
    assert result["result"].status == "failed"
    assert "Tool call limit" in result["result"].message


async def test_parent_retains_private_state_between_turns(
    api_config: ApiConfig, context: SupportContext
) -> None:
    support = build_support_graph(
        scripted_model(finish("Which date?", "needs_input")),
        api_config,
        checkpointer=True,
    )
    parent = StateGraph(SupportState, context_schema=SupportContext)
    parent.add_node("support", support)
    parent.add_edge(START, "support")
    parent.add_edge("support", END)
    graph = parent.compile(checkpointer=InMemorySaver())
    await graph.ainvoke({"messages": [HumanMessage("Book")]}, RUN, context=context)
    with pytest.raises(PermissionError):
        await graph.ainvoke(
            {"messages": [HumanMessage("Tomorrow")]},
            RUN,
            context=SupportContext(user_id="bob"),
        )


async def test_api_change_invalidates_approval(
    api_config: ApiConfig, context: SupportContext
) -> None:
    saver = InMemorySaver()
    graph = build_support_graph(scripted_model(booking()), api_config, saver)
    pending = await graph.ainvoke(
        {"messages": [HumanMessage("Book")]}, RUN, context=context
    )
    replaced = build_support_graph(
        scripted_model(),
        api_config.model_copy(update={"base_url": "http://different.test"}),
        saver,
    )
    with pytest.raises(ValueError, match="configuration changed"):
        await replaced.ainvoke(approve(pending), RUN, context=context)
