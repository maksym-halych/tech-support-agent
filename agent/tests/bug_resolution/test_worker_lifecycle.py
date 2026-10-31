"""Process-lifetime guards using the real coordinator, graph and HTTP boundary."""

import asyncio
from dataclasses import replace

import pytest
from aiohttp import ClientSession
from aiohttp.test_utils import TestServer

from tech_support_agent.bug_resolution.contracts import PublicEventQuery, TeamReply
from tech_support_agent.bug_resolution.coordinator import (
    CaseCoordinator,
    CaseUnavailable,
)
from tech_support_agent.bug_resolution.worker import create_app
from tech_support_agent.config import BugSettings
from tests.bug_resolution.conftest import TOKEN, FakeServices, report, until
from tests.bug_resolution.test_team_decisions import TeamHarness, callback, start


async def test_public_backend_reattach_does_not_restart_private_work() -> None:
    harness = TeamHarness()
    coordinator, case_id = await start(harness)
    try:
        await until(lambda: coordinator.records[case_id].team_wait)
        for message in ("same-report", "edited-after-backend-restart"):
            receipt = await coordinator.accept_case(report(message=message))
            assert receipt.status == "duplicate" and receipt.case_id == case_id
        event = await coordinator.public_event(
            case_id, PublicEventQuery(source_thread_id="client-thread")
        )
        assert event.status == "handed_off"
        assert len(harness.investigated) == 1 and not harness.executed
        assert (await coordinator.accept_case(report("new-thread"))).status == "busy"
    finally:
        await coordinator.close()


async def test_restart_has_new_run_and_rejects_old_reply_and_button() -> None:
    first = TeamHarness()
    old, old_case = await start(first)
    await until(lambda: old.records[old_case].team_wait)
    stale = callback(first, "approve")
    await old.close()
    second = TeamHarness()
    new, new_case = await start(second)
    try:
        await until(lambda: new.records[new_case].team_wait)
        assert new.run_id != old.run_id and new_case != old_case
        assert (await new.consume_callback(stale)).status == "stale"
        assert (
            await new.consume_reply(
                TeamReply(
                    update_id=2,
                    user_id=42,
                    chat_id=42,
                    chat_type="private",
                    reply_to_message_id=1,
                    text="approve",
                )
            )
        ).status == "stale"
        assert not second.executed
        assert all(record.task.done() for record in old.records.values())
    finally:
        await new.close()


@pytest.mark.parametrize("waiting", ["client", "investigation", "delivery"])
async def test_shutdown_cancels_owned_tasks_during_intake_waits(waiting: str) -> None:
    services = FakeServices()
    if waiting == "investigation":
        services.investigate_gate = asyncio.Event()
    if waiting == "delivery":
        services.question = None
        services.delivery_gate = asyncio.Event()
    coordinator = CaseCoordinator(services.dependencies())
    receipt = await coordinator.accept_case(report())
    record = coordinator.records[receipt.case_id]
    await until(
        lambda: (
            record.events
            if waiting == "client"
            else ("team_report" if waiting == "delivery" else "investigate")
            in services.trace
        )
    )
    assert (await coordinator.accept_case(report("second"))).status == "busy"
    await coordinator.close()
    await until(lambda: coordinator.active is None)
    assert record.task.done() and record.failed and coordinator.closed
    with pytest.raises(CaseUnavailable):
        await coordinator.accept_case(report("third"))


async def test_execution_cancel_waits_for_cleanup_before_releasing_slot() -> None:
    harness = TeamHarness()
    executing, cancelled, cleanup_started, cleanup_release = (
        asyncio.Event() for _ in range(4)
    )

    async def execute(case):
        executing.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    async def cleanup(case_id):
        assert cancelled.is_set()
        cleanup_started.set()
        await cleanup_release.wait()

    coordinator = CaseCoordinator(
        replace(harness.services(), execute_approved=execute, cleanup=cleanup)
    )
    receipt = await coordinator.accept_case(report())
    await until(lambda: coordinator.records[receipt.case_id].team_wait)
    await coordinator.consume_callback(callback(harness, "approve"))
    await executing.wait()
    closing = asyncio.create_task(coordinator.close())
    try:
        await cleanup_started.wait()
        assert not closing.done() and coordinator.active == receipt.case_id
    finally:
        cleanup_release.set()
        await closing
    await until(lambda: coordinator.active is None)
    assert coordinator.records[receipt.case_id].task.done()


async def test_cleanup_failure_closes_admission_instead_of_reusing_dirty_resources() -> (
    None
):
    harness = TeamHarness()

    async def cleanup(case_id):
        raise RuntimeError("private cleanup failure")

    coordinator = CaseCoordinator(replace(harness.services(), cleanup=cleanup))
    receipt = await coordinator.accept_case(report())
    try:
        await until(lambda: coordinator.records[receipt.case_id].team_wait)
        await coordinator.consume_callback(callback(harness, "reject"))
        await until(lambda: coordinator.active is None)
        assert coordinator.closed and coordinator.records[receipt.case_id].failed
        with pytest.raises(CaseUnavailable):
            await coordinator.accept_case(report("new-thread"))
    finally:
        await coordinator.close()


async def test_http_shutdown_closes_coordinator_and_never_exposes_resume() -> None:
    harness = TeamHarness()
    coordinator = CaseCoordinator(harness.services())
    settings = BugSettings(_env_file=None, worker_token=TOKEN)
    async with TestServer(create_app(settings, coordinator)) as server:
        async with ClientSession(
            headers={"Authorization": f"Bearer {TOKEN}"}
        ) as client:
            response = await client.post(
                server.make_url("/cases"), json=report().model_dump()
            )
            case_id = (await response.json())["case_id"]
            await until(lambda: coordinator.records[case_id].team_wait)
            for path in ("/resume", "/approval", f"/cases/{case_id}/state"):
                response = await client.post(
                    server.make_url(path), json={"approve": True}
                )
                assert response.status == 404
            assert not harness.executed
    assert coordinator.closed
    assert coordinator.records[case_id].task.done()
    assert not coordinator.tokens
