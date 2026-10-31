"""Real Agent Server -> HTTP worker -> Docker -> private decision boundary.

Telegram delivery is a deterministic test double. These rejection-path checks do
not claim live model reasoning, successful fixes, remote PRs, or GitHub CI.
"""

from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path

import pytest
from aiohttp.test_utils import TestServer

from tech_support_agent.bug_resolution.adapters.commands import run_command
from tech_support_agent.bug_resolution.adapters.private_api import PrivateAPI
from tech_support_agent.bug_resolution.adapters.sandbox import (
    DockerSandbox,
    capture_profile,
)
from tech_support_agent.bug_resolution.contracts import (
    CaseSnapshot,
    InvestigationResult,
)
from tech_support_agent.bug_resolution.coordinator import CaseCoordinator
from tech_support_agent.bug_resolution.sandbox.protocol import Click, Empty, Select
from tech_support_agent.bug_resolution.worker import create_app
from tech_support_agent.config import BugSettings, DemoSettings
from tests.bug_resolution.conftest import TOKEN, report, until
from tests.bug_resolution.integration.test_demo_sandbox import (
    docker_image as docker_image,  # noqa: PLC0414
)
from tests.bug_resolution.integration.test_demo_sandbox import fixture_repository, ref
from tests.bug_resolution.integration.test_public_server import (
    Server,
    assert_public,
    stream_run,
)
from tests.bug_resolution.integration.test_public_server import (
    server as server,  # noqa: PLC0414
)
from tests.bug_resolution.test_team_decisions import TeamHarness, callback

pytestmark = [pytest.mark.server, pytest.mark.docker]


@pytest.fixture(params=["continue", "both"])
async def boundary(request: pytest.FixtureRequest, docker_image: str, tmp_path: Path):
    scenario = request.param
    repo = tmp_path / "demo"
    await fixture_repository(repo)
    profile = await capture_profile(
        DemoSettings(
            _env_file=None,
            demo_repo="isolated-fixture/quiz",
            demo_repo_dir=repo,
            sandbox_image=docker_image,
        ),
        scenario,
    )
    harness = TeamHarness(scenario=scenario)
    containers = []

    async def investigate(case: CaseSnapshot) -> InvestigationResult:
        harness.investigated.append(case)
        sandbox = await DockerSandbox.prepare(profile)
        containers.append(sandbox)
        page = await sandbox.request(Empty(op="inspect"))
        page = await sandbox.request(Click(ref=ref(page, "Both")))
        page = await sandbox.request(Click(ref=ref(page, "Continue")))
        page = await sandbox.request(
            Select(ref=ref(page, "Grade", "combobox"), value="3")
        )
        button = next(
            element for element in page.elements if element.name == "Continue"
        )
        if scenario == "continue":
            assert button.disabled
        else:
            assert not button.disabled
            page = await sandbox.request(Click(ref=button.ref))
            page = await sandbox.request(
                Select(
                    ref=ref(page, "Learning goal", "combobox"), value="Build confidence"
                )
            )
            page = await sandbox.request(Click(ref=ref(page, "See demo summary")))
            assert "Math" in page.text and "Reading" not in page.text
        image = await sandbox.screenshot(tmp_path)
        return InvestigationResult(
            status="confirmed",
            summary="Deterministic browser observed the prepared bug; not live model reasoning",
            steps=[
                "Select Both, continue, choose Grade 3",
                "Inspect grade/summary behavior",
            ],
            artifact_ids=[image.artifact_id],
        )

    async def cleanup(case_id: str) -> None:
        for sandbox in containers:
            await sandbox.close()

    coordinator = CaseCoordinator(
        replace(
            harness.services(),
            investigate=investigate,
            cleanup=cleanup,
            base_sha=profile.base_sha,
        )
    )
    try:
        yield coordinator, harness, containers
    finally:
        await coordinator.close()
        for sandbox in containers:
            assert sandbox.closed
            assert not (
                await run_command(
                    "docker",
                    "ps",
                    "--all",
                    "--quiet",
                    "--filter",
                    f"name=^{sandbox.name}$",
                )
            ).strip()


@pytest.fixture
async def api(boundary) -> AsyncIterator[PrivateAPI]:
    coordinator, _, _ = boundary
    settings = BugSettings(
        _env_file=None, worker_token=TOKEN, poll_timeout=0.3, poll_interval=0.01
    )
    async with TestServer(
        create_app(settings, coordinator), host="127.0.0.1"
    ) as worker:
        yield PrivateAPI(
            settings.model_copy(
                update={"worker_url": str(worker.make_url("")).rstrip("/")}
            )
        )


async def test_real_process_http_docker_reject_and_privacy(
    server: Server, boundary
) -> None:
    coordinator, harness, containers = boundary
    client, _ = server
    thread_id = (await client.threads.create())["thread_id"]
    await stream_run(
        client,
        thread_id,
        {
            "messages": [
                {
                    "type": "human",
                    "content": "The quiz loses my choices or blocks Continue",
                }
            ]
        },
    )
    await until(lambda: harness.sends, timeout=30)
    case_id = coordinator.bindings[thread_id]
    await until(lambda: coordinator.records[case_id].team_wait)
    assert containers and not containers[0].closed
    assert (await coordinator.accept_case(report("second-thread"))).status == "busy"
    action = "approve" if harness.scenario == "continue" else "reject"
    assert (
        await coordinator.consume_callback(callback(harness, action, user_id=99))
    ).status == "stale"
    assert not harness.executed
    await coordinator.consume_callback(callback(harness, "reject", update=2))
    await until(lambda: coordinator.active is None, timeout=30)
    assert coordinator.records[case_id].final.phase == "rejected"
    assert not harness.executed and containers[0].closed
    # Reload/rejoin of the public thread sees the stored handoff, not private rejection.
    await stream_run(client, thread_id)
    state = await client.threads.get_state(thread_id)
    assert_public(state)
    assert_public(await client.threads.get_history(thread_id))
    assert len(coordinator.records) == 1 and len(harness.investigated) == 1
    assert len(coordinator.records[case_id].events) == 1
