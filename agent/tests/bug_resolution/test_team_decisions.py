import asyncio
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer
from pydantic import SecretStr

from tech_support_agent.bug_resolution.adapters.telegram import (
    TelegramClient,
    TelegramUpdate,
)
from tech_support_agent.bug_resolution.contracts import (
    CaseSnapshot,
    Clarification,
    ExecutionOutcome,
    HandedOff,
    InvestigationResult,
    PlanProposal,
    TeamCallback,
    TeamDelivery,
    TeamMessage,
    TeamReply,
)
from tech_support_agent.bug_resolution.coordinator import CaseCoordinator
from tech_support_agent.bug_resolution.graph import Phase3Services
from tests.bug_resolution.conftest import report, until
from tests.bug_resolution.test_intake import answer
from tests.bug_resolution.test_investigation import confirmed, proposal


@dataclass
class TeamHarness:
    scenario: str = "continue"
    result: InvestigationResult = field(default_factory=confirmed)
    sends: list[TeamMessage] = field(default_factory=list)
    executed: list[CaseSnapshot] = field(default_factory=list)
    investigated: list[CaseSnapshot] = field(default_factory=list)
    choice: str | None = None
    gate: asyncio.Event | None = None
    fail_investigation: bool = False
    fail_send: bool = False

    async def investigate(self, case: CaseSnapshot) -> InvestigationResult:
        self.investigated.append(case)
        if self.fail_investigation:
            raise RuntimeError("SECRET infrastructure error")
        return self.result

    async def propose(self, case: CaseSnapshot) -> PlanProposal:
        return proposal(product_choice=self.choice)

    async def send(self, message: TeamMessage) -> TeamDelivery:
        if self.fail_send:
            raise RuntimeError("SECRET delivery failure")
        self.sends.append(message)
        message_id = len(self.sends)
        if self.gate:
            await self.gate.wait()
        return TeamDelivery(message_id=message_id)

    async def execute(self, case: CaseSnapshot) -> ExecutionOutcome:
        self.executed.append(case)
        return ExecutionOutcome(
            status="blocked",
            summary="Test downstream received approval; no production fix executed",
        )

    def services(self, *, downstream: bool = True) -> Phase3Services:
        return Phase3Services(
            self.investigate,
            self.propose,
            self.send,
            42,
            42,
            scenario=self.scenario,
            base_sha="a" * 40,
            execute_approved=self.execute if downstream else None,
        )


def callback(
    harness: TeamHarness,
    action: str,
    *,
    update: int = 1,
    message: int | None = None,
    **updates: object,
) -> TeamCallback:
    selected = harness.sends[(message or len(harness.sends)) - 1]
    button = next(
        button for button in selected.buttons if button.text.lower().startswith(action)
    )
    return TeamCallback(
        **{
            "update_id": update,
            "callback_id": f"query-{update}",
            "token": button.token,
            "user_id": 42,
            "chat_id": 42,
            "chat_type": "private",
            "message_id": message or len(harness.sends),
            **updates,
        }
    )


async def start(
    harness: TeamHarness, *, downstream: bool = True
) -> tuple[CaseCoordinator, str]:
    coordinator = CaseCoordinator(harness.services(downstream=downstream))
    receipt = await coordinator.accept_case(report())
    await until(lambda: harness.sends)
    return coordinator, receipt.case_id


async def test_no_response_holds_slot_and_reject_never_executes() -> None:
    harness = TeamHarness()
    coordinator, case_id = await start(harness)
    try:
        await until(lambda: coordinator.records[case_id].team_wait)
        assert (await coordinator.accept_case(report("other"))).status == "busy"
        await asyncio.sleep(0.02)
        assert (
            not harness.executed
            and len(harness.investigated) == 1
            and len(harness.sends) == 1
        )
        event = callback(harness, "reject")
        assert (await coordinator.consume_callback(event)).status == "accepted"
        assert (await coordinator.consume_callback(event)).status == "duplicate"
        await until(lambda: coordinator.active is None)
        assert not harness.executed
        assert coordinator.records[case_id].final.phase == "rejected"
        assert len(harness.sends) == 2
        assert len(coordinator.records[case_id].events) == 1
        assert isinstance(coordinator.records[case_id].events[0], HandedOff)
    finally:
        await coordinator.close()


@pytest.mark.parametrize(
    "change",
    [
        {"user_id": 99},
        {"chat_id": 99},
        {"chat_type": "group"},
        {"message_id": 99},
        {"token": "unknown-from-old-run"},
    ],
)
async def test_wrong_sender_chat_message_or_run_never_executes(
    change: dict[str, object],
) -> None:
    harness = TeamHarness()
    coordinator, case_id = await start(harness)
    try:
        await until(lambda: coordinator.records[case_id].team_wait)
        assert (
            await coordinator.consume_callback(callback(harness, "approve", **change))
        ).status == "stale"
        assert not harness.executed and coordinator.active == case_id
    finally:
        await coordinator.close()


async def test_approve_is_consumed_once_and_resume_does_not_resend() -> None:
    harness = TeamHarness()
    coordinator, case_id = await start(harness)
    try:
        await until(lambda: coordinator.records[case_id].team_wait)
        event = callback(harness, "approve")
        receipts = await asyncio.gather(
            *(coordinator.consume_callback(event) for _ in range(10))
        )
        assert [receipt.status for receipt in receipts].count("accepted") == 1
        await until(lambda: coordinator.active is None)
        assert len(harness.executed) == 1 and len(harness.sends) == 2
        case = harness.executed[0]
        assert case.approval.approved_by == 42 and case.approval.revision == 1
        assert case.approval.expected_behavior == case.plan.expected_behavior
        assert case.base_sha == "a" * 40 and case.attempt_count == 0
        assert len(coordinator.records[case_id].events) == 1
    finally:
        await coordinator.close()


async def test_early_callback_is_retained_until_actual_send_receipt_and_matching_wait() -> (
    None
):
    harness = TeamHarness(gate=asyncio.Event())
    coordinator, _case_id = await start(harness)
    try:
        task = asyncio.create_task(
            coordinator.consume_callback(callback(harness, "approve"))
        )
        await asyncio.sleep(0.01)
        assert not task.done() and not harness.executed
        harness.gate.set()
        assert (await task).status == "accepted"
        await until(lambda: coordinator.active is None)
        assert len(harness.executed) == 1
    finally:
        await coordinator.close()


@pytest.mark.parametrize("choice", ["combined", "separate"])
async def test_both_text_choice_requires_new_revision_button(choice: str) -> None:
    harness = TeamHarness(scenario="both", choice=choice)
    coordinator, case_id = await start(harness)
    try:
        await until(lambda: coordinator.records[case_id].team_wait)
        assert all(
            not button.text.startswith("Approve") for button in harness.sends[0].buttons
        )
        stale_reject = callback(harness, "reject")
        reply = TeamReply(
            update_id=2,
            user_id=42,
            chat_id=42,
            chat_type="private",
            reply_to_message_id=1,
            text=f"Choose {choice} plans",
        )
        assert (
            await coordinator.consume_reply(
                reply.model_copy(update={"reply_to_message_id": 99})
            )
        ).status == "stale"
        reply = reply.model_copy(update={"update_id": 3})
        assert (await coordinator.consume_reply(reply)).status == "accepted"
        await until(
            lambda: len(harness.sends) == 2 and coordinator.records[case_id].team_wait
        )
        assert not harness.executed
        assert coordinator.records[case_id].team_wait.plan_revision == 2
        assert (await coordinator.consume_callback(stale_reject)).status == "stale"
        assert (
            await coordinator.consume_callback(callback(harness, "approve", update=4))
        ).status == "accepted"
        await until(lambda: coordinator.active is None)
        assert harness.executed[0].plan.product_choice == choice
        assert harness.executed[0].approval.revision == 2
        assert len(coordinator.records[case_id].events) == 1
    finally:
        await coordinator.close()


async def test_discuss_invalidates_buttons_and_text_approve_is_not_permission() -> None:
    harness = TeamHarness()
    coordinator, case_id = await start(harness)
    try:
        await until(lambda: coordinator.records[case_id].team_wait)
        old = callback(harness, "approve", update=5)
        assert (
            await coordinator.consume_callback(callback(harness, "discuss"))
        ).status == "accepted"
        await until(
            lambda: len(harness.sends) == 2 and coordinator.records[case_id].team_wait
        )
        assert (await coordinator.consume_callback(old)).status == "stale"
        assert (
            await coordinator.consume_reply(
                TeamReply(
                    update_id=6,
                    user_id=42,
                    chat_id=42,
                    chat_type="private",
                    reply_to_message_id=2,
                    text="approve",
                )
            )
        ).status == "accepted"
        await until(
            lambda: len(harness.sends) == 3 and coordinator.records[case_id].team_wait
        )
        assert (
            not harness.executed
            and coordinator.records[case_id].team_wait.plan_revision == 2
        )
    finally:
        await coordinator.close()


async def test_restart_invalidates_all_old_tokens() -> None:
    first = TeamHarness()
    old, _ = await start(first)
    event = callback(first, "approve")
    await old.close()
    second = TeamHarness()
    new, case_id = await start(second)
    try:
        assert (await new.consume_callback(event)).status == "stale"
        assert not second.executed and new.active == case_id
    finally:
        await new.close()


@pytest.mark.parametrize(
    "status", ["unconfirmed", "needs_details", "blocked", "exception"]
)
async def test_one_clarification_or_infrastructure_handoff_without_write(
    status: str,
) -> None:
    harness = TeamHarness(
        result=InvestigationResult(
            status="blocked" if status == "exception" else status,
            summary="Could not reproduce"
            if status != "blocked"
            else "Site unavailable",
            question="Which grade?",
        ),
        fail_investigation=status == "exception",
    )
    coordinator = CaseCoordinator(harness.services())
    try:
        receipt = await coordinator.accept_case(report())
        record = coordinator.records[receipt.case_id]
        await until(lambda: record.events or record.terminal)
        if status in ("unconfirmed", "needs_details"):
            assert isinstance(record.events[0], Clarification)
            await coordinator.clarify(receipt.case_id, answer())
        await until(lambda: coordinator.active is None)
        assert not harness.executed and len(harness.sends) == 1
        assert len(harness.investigated) == (
            2 if status in ("unconfirmed", "needs_details") else 1
        )
        assert isinstance(record.events[-1], HandedOff)
        assert not harness.sends[0].buttons and "SECRET" not in harness.sends[0].text
    finally:
        await coordinator.close()


async def test_missing_downstream_is_truthful_and_failed_delivery_has_no_handoff() -> (
    None
):
    harness = TeamHarness()
    coordinator, case_id = await start(harness, downstream=False)
    try:
        await until(lambda: coordinator.records[case_id].team_wait)
        await coordinator.consume_callback(callback(harness, "approve"))
        await until(lambda: coordinator.active is None)
        assert "not configured" in harness.sends[-1].text and not harness.executed
    finally:
        await coordinator.close()
    harness = TeamHarness(fail_send=True)
    coordinator = CaseCoordinator(harness.services())
    try:
        receipt = await coordinator.accept_case(report())
        await until(lambda: coordinator.active is None)
        assert coordinator.records[receipt.case_id].failed
        assert not coordinator.records[receipt.case_id].events
    finally:
        await coordinator.close()


async def test_fake_telegram_http_real_graph_ack_and_reply_instructions(
    tmp_path: Path,
) -> None:
    requests = []

    async def handle(request: web.Request) -> web.Response:
        body = await request.json()
        method = request.match_info["method"]
        requests.append((method, body))
        result = (
            True
            if method == "answerCallbackQuery"
            else {
                "message_id": len(requests),
                "chat": {"id": 42, "type": "private"},
                "from": {"id": 100, "is_bot": True},
            }
        )
        return web.json_response({"ok": True, "result": result})

    app = web.Application()
    app.router.add_post("/bottest/{method}", handle)
    async with TestServer(app) as server, ClientSession() as session:
        telegram = TelegramClient(
            session,
            SecretStr("test"),
            42,
            42,
            {},
            tmp_path,
            api_root=str(server.make_url("")).rstrip("/"),
        )
        harness = TeamHarness()

        async def send(message: TeamMessage) -> TeamDelivery:
            harness.sends.append(message)
            return await telegram.send(message.model_copy(update={"artifact_ids": []}))

        coordinator = CaseCoordinator(
            Phase3Services(
                harness.investigate,
                harness.propose,
                send,
                42,
                42,
                execute_approved=harness.execute,
            )
        )
        try:
            receipt = await coordinator.accept_case(report())
            await until(lambda: coordinator.records[receipt.case_id].team_wait)
            event = callback(harness, "approve")
            update = TelegramUpdate.model_validate(
                {
                    "update_id": 1,
                    "callback_query": {
                        "id": event.callback_id,
                        "from": {"id": 42, "is_bot": False},
                        "data": event.token,
                        "message": {
                            "message_id": 1,
                            "chat": {"id": 42, "type": "private"},
                        },
                    },
                }
            )
            await telegram.handle_update(
                update, coordinator.consume_callback, coordinator.consume_reply
            )
            await until(lambda: coordinator.active is None)
            assert len(harness.executed) == 1
            assert requests[1][0] == "answerCallbackQuery"
            assert (
                requests[0][1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
                == event.token
            )
            assert "parse_mode" not in requests[0][1]
            text_update = TelegramUpdate.model_validate(
                {
                    "update_id": 2,
                    "message": {
                        "message_id": 10,
                        "chat": {"id": 42, "type": "private"},
                        "from": {"id": 42, "is_bot": False},
                        "text": "approve",
                    },
                }
            )
            await telegram.handle_update(
                text_update, coordinator.consume_callback, coordinator.consume_reply
            )
            assert "Text alone never approves" in requests[-1][1]["text"]
            assert len(harness.executed) == 1
        finally:
            await coordinator.close()
