"""Real locked Agent Server on Python 3.14; only private services are doubles."""

import asyncio
import json
import os
import socket
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from langgraph_cli.config import validate_config
from langgraph_sdk import get_client
from langgraph_sdk.client import LangGraphClient

from tech_support_agent.bug_resolution.adapters.private_api import PrivateAPI
from tech_support_agent.bug_resolution.contracts import HANDOFF_TEXT
from tech_support_agent.bug_resolution.coordinator import CaseCoordinator
from tests.bug_resolution.conftest import PRIVATE_SENTINEL, TOKEN, FakeServices, until

pytestmark = pytest.mark.server
AGENT_DIR = Path(__file__).resolve().parents[3]
type Server = tuple[LangGraphClient, httpx.AsyncClient]


@pytest.fixture
async def server(api: PrivateAPI, tmp_path: Path) -> AsyncIterator[Server]:
    assert sys.version_info[:2] == (3, 14)
    config = json.loads((AGENT_DIR / "langgraph.bug-demo.json").read_text())
    assert validate_config(config)["python_version"] == "3.14"
    assert (
        validate_config(json.loads((AGENT_DIR / "langgraph.json").read_text()))[
            "python_version"
        ]
        == "3.14"
    )
    assert config["graphs"] == {
        "bug_demo": "tech_support_agent.bug_resolution.demo_graph:build_agent"
    }
    config["env"] = {}
    config["dependencies"] = [str(AGENT_DIR)]
    config_path = tmp_path / "langgraph.json"
    config_path.write_text(json.dumps(config))
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(tmp_path),
        "PYTHONUNBUFFERED": "1",
        "LANGSMITH_TRACING": "false",
        "LANGCHAIN_TRACING_V2": "false",
        "LANGGRAPH_CLI_NO_ANALYTICS": "1",
        "BUG_WORKER_URL": api.settings.worker_url,
        "BUG_WORKER_TOKEN": TOKEN,
        "BUG_POLL_TIMEOUT": "0.3",
        "BUG_POLL_INTERVAL": "0.01",
    }
    log_path = tmp_path / "agent-server.log"
    with log_path.open("wb") as log:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            str(Path(__file__).with_name("server_process.py")),
            "dev",
            "--config",
            str(config_path),
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--no-reload",
            "--no-browser",
            cwd=tmp_path,
            env=env,
            stdout=log,
            stderr=asyncio.subprocess.STDOUT,
        )
        url = f"http://127.0.0.1:{port}"
        try:
            async with httpx.AsyncClient(
                base_url=url, timeout=5, trust_env=False
            ) as http:
                try:
                    async with asyncio.timeout(30):
                        while True:
                            if process.returncode is not None:
                                pytest.fail(
                                    f"Agent Server exited {process.returncode}: {log_path.read_text()}"
                                )
                            try:
                                if (await http.get("/ok")).status_code == 200:
                                    break
                            except httpx.TransportError:
                                pass
                            await asyncio.sleep(0.1)
                except TimeoutError:
                    pytest.fail(
                        f"Agent Server startup timed out: {log_path.read_text()}"
                    )
                yield get_client(url=url), http
        finally:
            if process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), 10)
                except TimeoutError:
                    process.kill()
                    await process.wait()


def assert_public(value: object) -> None:
    serialized = json.dumps(value, default=str)
    for forbidden in (
        PRIVATE_SENTINEL,
        TOKEN,
        "private_summary",
        "approved_by",
        "CaseSnapshot",
        "team_report",
        "wait_for_client",
    ):
        assert forbidden not in serialized


async def stream_run(
    client: LangGraphClient,
    thread_id: str,
    input: dict | None = None,
    checkpoint: dict | None = None,
    config: dict | None = None,
) -> list[dict]:
    events = []
    async for event in client.runs.stream(
        thread_id,
        "bug_demo",
        input=input,
        checkpoint=checkpoint,
        config=config,
        stream_mode=["values", "updates", "messages", "debug"],
        stream_subgraphs=True,
        stream_resumable=True,
    ):
        assert event.event != "error", event.data
        events.append({"event": event.event, "data": event.data})
    assert_public(events)
    return events


async def test_server_submit_edit_regenerate_clarify_and_privacy(
    server: Server, coordinator: CaseCoordinator, services: FakeServices
) -> None:
    client, http = server
    thread = await client.threads.create()
    thread_id = thread["thread_id"]
    # Same no-ID edit shape as the checked-in frontend; locked SDK + server normalize it.
    await stream_run(
        client,
        thread_id,
        {"messages": [{"type": "human", "content": "Continue fails"}]},
        config={"configurable": {"thread_id": "caller-forged-thread"}},
    )
    state = await client.threads.get_state(thread_id)
    assert state["values"]["messages"][0]["id"]
    assert state["values"]["messages"][-1]["content"] == services.question
    case_id = state["values"]["bridge"]["case_id"]
    assert coordinator.records[case_id].initial.request.source_thread_id == thread_id
    original_id = coordinator.records[case_id].initial.request.source_message_id
    history = await client.threads.get_history(thread_id)
    before_bridge = next(
        snapshot for snapshot in history if snapshot["next"] == ["bug_bridge"]
    )
    before_input = next(
        snapshot for snapshot in history if not snapshot["values"].get("messages")
    )
    await stream_run(client, thread_id, checkpoint=before_bridge["checkpoint"])
    assert services.trace == ["investigate"]
    regenerated = await client.threads.get_state(thread_id)
    assert (
        regenerated["values"]["messages"][-1]["id"]
        == state["values"]["messages"][-1]["id"]
    )
    await stream_run(
        client,
        thread_id,
        {"messages": [{"type": "human", "content": "Edited bug report"}]},
        checkpoint=before_input["checkpoint"],
    )
    edited = await client.threads.get_state(thread_id)
    assert edited["values"]["messages"][0]["id"] != original_id
    assert edited["values"]["bridge"]["case_id"] == case_id
    assert len(coordinator.records) == 1
    assert services.trace == ["investigate"]
    await stream_run(
        client, thread_id, {"messages": [{"type": "human", "content": "Grade 3"}]}
    )
    final = await client.threads.get_state(thread_id)
    assert final["values"]["messages"][-1]["content"] == HANDOFF_TEXT
    await until(lambda: coordinator.active is None)
    assert services.trace == ["investigate", "investigate", "team_report"]
    assert_public(final)
    assert_public(await client.threads.get_history(thread_id))
    assert_public(await client.assistants.search())
    assistants = await client.assistants.search()
    assert {assistant["graph_id"] for assistant in assistants} == {"bug_demo"}
    private_thread = await http.get(f"/threads/{case_id}/state")
    assert private_thread.status_code in (404, 422)
    # Terminal worker binding survives rollback of all public bookkeeping.
    await stream_run(
        client,
        thread_id,
        {"messages": [{"type": "human", "content": "Another edit"}]},
        checkpoint=before_input["checkpoint"],
    )
    assert len(coordinator.records) == 1
    assert services.trace == ["investigate", "investigate", "team_report"]


async def test_server_disconnect_rejoin_keeps_worker_alive(
    server: Server, coordinator: CaseCoordinator, services: FakeServices
) -> None:
    client, http = server
    services.question = None
    services.investigate_gate = asyncio.Event()
    thread_id = (await client.threads.create())["thread_id"]
    async with http.stream(
        "POST",
        f"/threads/{thread_id}/runs/stream",
        json={
            "assistant_id": "bug_demo",
            "input": {
                "messages": [
                    {"type": "human", "content": "Bug", "id": "disconnect-message"}
                ]
            },
            "stream_mode": ["values"],
            "stream_resumable": True,
            "on_disconnect": "continue",
        },
    ) as response:
        assert response.status_code == 200
        async for line in response.aiter_lines():
            if line.startswith("data:") and "run_id" in line:
                run_id = json.loads(line.removeprefix("data:"))["run_id"]
                break
        else:
            pytest.fail("No run metadata received before disconnect")
    await until(lambda: thread_id in coordinator.bindings)
    case_id = coordinator.bindings[thread_id]
    assert not coordinator.records[case_id].task.done()
    services.investigate_gate.set()
    async with http.stream(
        "GET", f"/threads/{thread_id}/runs/{run_id}/stream"
    ) as response:
        assert response.status_code == 200
        rejoined = await response.aread()
        assert (
            PRIVATE_SENTINEL.encode() not in rejoined and TOKEN.encode() not in rejoined
        )
    await until(lambda: coordinator.active is None)
    # Reattach using the same persisted source; no second case after reconnect.
    await stream_run(
        client,
        thread_id,
        {"messages": [{"type": "human", "content": "Bug", "id": "disconnect-message"}]},
    )
    final = await client.threads.get_state(thread_id)
    assert any(
        message["content"] == HANDOFF_TEXT for message in final["values"]["messages"]
    )
    assert len(coordinator.records) == 1
    assert services.trace == ["investigate", "team_report"]
    assert_public(final)


async def test_server_delivery_error_is_neutral_and_private(
    server: Server, services: FakeServices, coordinator: CaseCoordinator
) -> None:
    client, _ = server
    services.question = None
    services.fail_delivery = True
    thread_id = (await client.threads.create())["thread_id"]
    await stream_run(
        client, thread_id, {"messages": [{"type": "human", "content": "Bug"}]}
    )
    state = await client.threads.get_state(thread_id)
    assert not state["values"]["bridge"]["handed_off"]
    assert HANDOFF_TEXT not in json.dumps(state)
    assert_public(state)
    assert_public(await client.threads.get_history(thread_id))
    await until(lambda: coordinator.active is None)
