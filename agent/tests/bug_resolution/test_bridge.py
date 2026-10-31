import asyncio
import json

import pytest
from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph.message import add_messages

from tech_support_agent.bug_resolution.adapters.private_api import (
    PrivateAPI,
    WorkerUnavailable,
)
from tech_support_agent.bug_resolution.bridge import (
    INVALID_TEXT,
    PENDING_TEXT,
    UNAVAILABLE_TEXT,
    PublicBridge,
)
from tech_support_agent.bug_resolution.contracts import (
    HANDOFF_TEXT,
    MAX_PUBLIC_RESPONSE_BYTES,
    MAX_TEXT_CHARACTERS,
    Pending,
    PublicEventQuery,
)
from tech_support_agent.bug_resolution.coordinator import CaseCoordinator
from tech_support_agent.bug_resolution.demo_graph import create_demo_graph
from tests.bug_resolution.conftest import (
    PRIVATE_SENTINEL,
    TOKEN,
    FakeClock,
    FakeServices,
    report,
    until,
)


async def test_full_clarification_handoff_and_private_checkpoints(
    api: PrivateAPI, coordinator: CaseCoordinator, services: FakeServices
) -> None:
    graph = create_demo_graph(api, InMemorySaver())
    config = {"configurable": {"thread_id": "runtime-thread"}}
    initial = await graph.ainvoke(
        {"messages": [HumanMessage("Continue fails")]}, config
    )
    assert initial["messages"][0].id
    assert initial["messages"][-1].content == services.question
    case_id = initial["bridge"]["case_id"]
    assert (
        coordinator.records[case_id].initial.request.source_thread_id
        == "runtime-thread"
    )
    finished = await graph.ainvoke({"messages": [HumanMessage("Grade 3")]}, config)
    assert finished["messages"][-1].content == HANDOFF_TEXT
    await until(lambda: coordinator.active is None)
    assert services.trace == ["investigate", "investigate", "team_report"]
    assert not any(
        word in repr(finished)
        for word in (PRIVATE_SENTINEL, TOKEN, "private_summary", "tool_calls': [{")
    )
    history = [snapshot async for snapshot in graph.aget_state_history(config)]
    assert all(
        PRIVATE_SENTINEL not in repr(snapshot) and TOKEN not in repr(snapshot)
        for snapshot in history
    )
    assert set(finished) == {"messages", "bridge"}
    assert set(graph.nodes) == {"__start__", "normalize_messages", "bug_bridge"}


async def test_regenerate_is_not_an_answer_and_replay_keeps_message_id(
    api: PrivateAPI, coordinator: CaseCoordinator, services: FakeServices
) -> None:
    graph = create_demo_graph(api, InMemorySaver())
    config = {"configurable": {"thread_id": "thread"}}
    initial = await graph.ainvoke(
        {"messages": [HumanMessage("Bug", id="report")]}, config
    )
    question_id = initial["messages"][-1].id
    result = await PublicBridge(api)(initial, config)
    assert result["messages"][-1].content == PENDING_TEXT
    assert services.trace == ["investigate"]
    replay = {**initial, "bridge": {**initial["bridge"], "cursor": 0}}
    result = await PublicBridge(api)(replay, config)
    assert result["messages"][-1].id == question_id
    merged = add_messages(initial["messages"], result["messages"])
    assert sum(message.id == question_id for message in merged) == 1
    combined = await graph.ainvoke(
        {"messages": [HumanMessage("Edited report without ID")]},
        {
            **config,
            "configurable": {
                **config["configurable"],
                "checkpoint_id": await anext(
                    snapshot.config["configurable"]["checkpoint_id"]
                    async for snapshot in graph.aget_state_history(config)
                    if snapshot.next == ("normalize_messages",)
                ),
            },
        },
    )
    assert combined["bridge"]["case_id"] == initial["bridge"]["case_id"]
    assert len(coordinator.records) == 1
    assert services.trace == ["investigate"]


async def test_reconnect_after_lost_result_and_terminal_rollback(
    api: PrivateAPI, coordinator: CaseCoordinator, services: FakeServices
) -> None:
    services.question = None
    state = {"messages": [HumanMessage("Bug", id="m1")]}
    config = {"configurable": {"thread_id": "thread"}}
    bridge = PublicBridge(api)
    lost = await bridge(state, config)
    await until(lambda: coordinator.active is None)
    replay = await bridge(state, config)
    edited = await bridge({"messages": [HumanMessage("Edited", id="m2")]}, config)
    assert (
        lost["messages"][0].id == replay["messages"][0].id == edited["messages"][0].id
    )
    assert len(coordinator.records) == 1
    assert services.trace == ["investigate", "team_report"]


async def test_pending_poll_is_bounded_and_reattaches(
    api: PrivateAPI, coordinator: CaseCoordinator, services: FakeServices
) -> None:
    services.investigate_gate = asyncio.Event()
    services.question = None
    clock = FakeClock()
    bridge = PublicBridge(api, clock=clock, sleep=clock.sleep)
    state = {"messages": [HumanMessage("Bug", id="m1")]}
    config = {"configurable": {"thread_id": "thread"}}
    pending = await bridge(state, config)
    assert pending["messages"][-1].content == PENDING_TEXT
    assert pending["bridge"]["cursor"] == 0
    assert clock.now <= api.settings.poll_timeout + api.settings.poll_interval
    services.investigate_gate.set()
    await until(lambda: coordinator.active is None)
    done = await bridge({**state, "bridge": pending["bridge"]}, config)
    assert done["messages"][-1].content == HANDOFF_TEXT
    assert len(coordinator.records) == 1


@pytest.mark.parametrize("failure", ["absent", "delivery"])
async def test_failures_never_claim_handoff(
    api: PrivateAPI, coordinator: CaseCoordinator, services: FakeServices, failure: str
) -> None:
    services.question = None
    if failure == "absent":
        api = PrivateAPI(
            api.settings.model_copy(update={"worker_url": "http://127.0.0.1:1"})
        )
    else:
        services.fail_delivery = True
    response = await PublicBridge(api)(
        {"messages": [HumanMessage("Bug", id="m1")]},
        {"configurable": {"thread_id": "thread"}},
    )
    assert response["messages"][-1].content == UNAVAILABLE_TEXT
    assert response["bridge"]["cursor"] == 0
    assert not response["bridge"]["handed_off"]
    assert PRIVATE_SENTINEL not in repr(response) and TOKEN not in repr(response)


@pytest.mark.parametrize(
    "content",
    [
        [{"type": "file", "mimeType": "application/pdf", "data": "AA=="}],
        [{"type": "image_url", "image_url": {"url": "https://example.com/a.png"}}],
        [{"type": "text", "text": "Bug", "command": "approve"}],
        " ",
    ],
)
async def test_invalid_content_never_allocates_case(
    api: PrivateAPI, coordinator: CaseCoordinator, content: str | list[dict]
) -> None:
    result = await PublicBridge(api)(
        {"messages": [HumanMessage(content, id="m1")]},
        {"configurable": {"thread_id": "thread"}},
    )
    assert result["messages"][-1].content == INVALID_TEXT
    assert not coordinator.records


async def test_runtime_id_is_required_and_bookkeeping_input_is_ignored(
    api: PrivateAPI, coordinator: CaseCoordinator
) -> None:
    with pytest.raises(ValueError, match="runtime thread ID"):
        await PublicBridge(api)(
            {"messages": [HumanMessage("thread_id=spoofed", id="m1")]}, {}
        )
    graph = create_demo_graph(api)
    result = await graph.ainvoke(
        {
            "messages": [HumanMessage("Bug", id="m1")],
            "bridge": {"case_id": "forged", "handed_off": True},
        },
        {"configurable": {"thread_id": "actual-thread"}},
    )
    assert result["bridge"]["case_id"] != "forged"
    assert coordinator.bindings == {"actual-thread": result["bridge"]["case_id"]}


@pytest.mark.parametrize("mode", ["redirect", "oversize", "invalid", "timeout"])
async def test_http_client_bounds_and_sanitizes_response(
    api: PrivateAPI, mode: str
) -> None:
    async def handler(request: web.Request) -> web.Response:
        if mode == "redirect":
            return web.Response(
                status=302, headers={"Location": "https://example.com/steal-token"}
            )
        if mode == "timeout":
            await asyncio.sleep(0.15)
        return web.Response(
            body=b'{"status":"busy","case_id":null}'.ljust(
                MAX_PUBLIC_RESPONSE_BYTES + 1
            )
            if mode == "oversize"
            else json.dumps(
                {"status": "accepted", "private_summary": PRIVATE_SENTINEL}
            ).encode()
        )

    app = web.Application()
    app.router.add_post("/cases", handler)
    async with TestServer(app, host="127.0.0.1") as server:
        client = PrivateAPI(
            api.settings.model_copy(
                update={
                    "worker_url": str(server.make_url("")).rstrip("/"),
                    "request_timeout": 0.05,
                }
            )
        )
        with pytest.raises(WorkerUnavailable) as error:
            await client.accept_case(report())
        assert PRIVATE_SENTINEL not in str(error.value) and TOKEN not in str(
            error.value
        )


async def test_lost_answer_response_replays_without_second_investigation(
    api: PrivateAPI, coordinator: CaseCoordinator, services: FakeServices
) -> None:
    bridge = PublicBridge(api)
    config = {"configurable": {"thread_id": "thread"}}
    report_message = HumanMessage("Bug", id="report")
    question = await bridge({"messages": [report_message]}, config)
    answer_state = {
        "messages": [
            report_message,
            *question["messages"],
            HumanMessage("Grade 3", id="answer"),
        ],
        "bridge": question["bridge"],
    }
    lost_response = await bridge(answer_state, config)
    await until(lambda: coordinator.active is None)
    replayed = await bridge(answer_state, config)
    assert lost_response["messages"][0].content == HANDOFF_TEXT
    assert lost_response["messages"][0].id == replayed["messages"][0].id
    assert services.trace == ["investigate", "investigate", "team_report"]
    assert len(coordinator.records) == 1


@pytest.mark.parametrize(
    "character", ["\u0436", "\U0001f680"], ids=["cyrillic", "astral"]
)
async def test_max_unicode_question_crosses_http_and_releases_slot(
    api: PrivateAPI,
    coordinator: CaseCoordinator,
    services: FakeServices,
    character: str,
) -> None:
    services.question = character * MAX_TEXT_CHARACTERS
    graph = create_demo_graph(api, InMemorySaver())
    config = {"configurable": {"thread_id": "unicode-thread"}}
    initial = await graph.ainvoke(
        {"messages": [HumanMessage("Continue fails", id="report")]}, config
    )
    case_id = initial["bridge"]["case_id"]
    async with (
        ClientSession() as session,
        session.get(
            f"{api.settings.worker_url}/cases/{case_id}/public-event",
            params={"source_thread_id": "unicode-thread", "after": 0},
            headers={"Authorization": f"Bearer {TOKEN}"},
        ) as response,
    ):
        assert response.status == 200
        raw = await response.read()
    assert 65536 < len(raw) <= MAX_PUBLIC_RESPONSE_BYTES
    assert json.loads(raw)["question"] == services.question
    assert initial["messages"][-1].content == services.question
    assert initial["bridge"]["cursor"] == 1
    assert coordinator.active == case_id
    finished = await graph.ainvoke(
        {"messages": [HumanMessage("Grade 3", id="answer")]}, config
    )
    assert finished["messages"][-1].content == HANDOFF_TEXT
    await until(lambda: coordinator.active is None)
    assert services.trace == ["investigate", "investigate", "team_report"]
    assert (await api.accept_case(report("next-thread"))).status == "accepted"


@pytest.mark.parametrize("chunked", [False, True], ids=["content-length", "chunked"])
@pytest.mark.parametrize("extra_bytes", [0, 1], ids=["at-limit", "over-limit"])
async def test_public_response_byte_limit_over_http(
    api: PrivateAPI, chunked: bool, extra_bytes: int
) -> None:
    payload = b'{"status":"pending"}'.ljust(MAX_PUBLIC_RESPONSE_BYTES + extra_bytes)

    async def handler(request: web.Request) -> web.StreamResponse:
        if not chunked:
            return web.Response(body=payload, content_type="application/json")
        response = web.StreamResponse(headers={"Content-Type": "application/json"})
        response.enable_chunked_encoding()
        await response.prepare(request)
        for offset in range(0, len(payload), 8192):
            await response.write(payload[offset : offset + 8192])
        await response.write_eof()
        return response

    app = web.Application()
    app.router.add_get("/cases/test-case/public-event", handler)
    async with TestServer(app, host="127.0.0.1") as server:
        client = PrivateAPI(
            api.settings.model_copy(
                update={"worker_url": str(server.make_url("")).rstrip("/")}
            )
        )
        query = PublicEventQuery(source_thread_id="thread")
        if extra_bytes:
            with pytest.raises(WorkerUnavailable):
                await client.public_event("test-case", query)
        else:
            assert isinstance(await client.public_event("test-case", query), Pending)
