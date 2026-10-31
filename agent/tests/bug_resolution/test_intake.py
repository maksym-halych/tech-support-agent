import asyncio
import base64
from io import BytesIO

import pytest
from aiohttp import ClientSession
from PIL import Image
from pydantic import ValidationError

from tech_support_agent.bug_resolution.adapters.private_api import PrivateAPI
from tech_support_agent.bug_resolution.contracts import (
    MAX_BODY_BYTES,
    CaseRequest,
    Clarification,
    ClarificationRequest,
    DeliveryReceipt,
    HandedOff,
    ImageAttachment,
    Pending,
    PublicEventQuery,
)
from tech_support_agent.bug_resolution.coordinator import (
    CaseCoordinator,
    CaseNotFound,
    CaseUnavailable,
)
from tech_support_agent.bug_resolution.graph import (
    IntakeServices,
    unavailable_delivery,
    unavailable_investigator,
)
from tech_support_agent.config import BugSettings
from tests.bug_resolution.conftest import (
    PRIVATE_SENTINEL,
    TOKEN,
    FakeServices,
    report,
    until,
)


def answer(
    cursor: int = 1, thread: str = "client-thread", message: str = "answer-1"
) -> ClarificationRequest:
    return ClarificationRequest(
        source_thread_id=thread,
        source_message_id=message,
        clarification_cursor=cursor,
        answer="Grade 3",
    )


async def test_admission_binding_concurrent_and_terminal_release(
    coordinator: CaseCoordinator, services: FakeServices
) -> None:
    receipts = await asyncio.gather(
        *(coordinator.accept_case(report()) for _ in range(10))
    )
    assert [item.status for item in receipts].count("accepted") == 1
    assert len({item.case_id for item in receipts}) == 1
    case_id = receipts[0].case_id
    await until(lambda: coordinator.records[case_id].events)
    assert (await coordinator.accept_case(report("other-thread"))).status == "busy"
    assert (
        await coordinator.accept_case(report(message="edited-no-original-id"))
    ).status == "duplicate"
    assert (await coordinator.clarify(case_id, answer())).status == "accepted"
    await until(lambda: coordinator.active is None)
    assert services.trace == ["investigate", "investigate", "team_report"]
    assert coordinator.records[case_id].task.done()
    assert (
        await coordinator.accept_case(report(message="another-edit"))
    ).case_id == case_id
    assert (await coordinator.accept_case(report("new-scenario"))).status == "accepted"
    assert len(coordinator.records) == 2


async def test_wrong_thread_stale_and_concurrent_answers(
    coordinator: CaseCoordinator, services: FakeServices
) -> None:
    receipt = await coordinator.accept_case(report())
    await until(lambda: coordinator.records[receipt.case_id].events)
    with pytest.raises(CaseNotFound):
        await coordinator.public_event(
            receipt.case_id, PublicEventQuery(source_thread_id="wrong")
        )
    with pytest.raises(CaseNotFound):
        await coordinator.clarify(receipt.case_id, answer(thread="wrong"))
    assert (
        await coordinator.clarify(receipt.case_id, answer(cursor=2))
    ).status == "stale"
    assert (
        await coordinator.clarify(receipt.case_id, answer(message="report-1"))
    ).status == "stale"
    receipts = await asyncio.gather(
        *(coordinator.clarify(receipt.case_id, answer()) for _ in range(10))
    )
    assert [item.status for item in receipts].count("accepted") == 1
    assert [item.status for item in receipts].count("duplicate") == 9
    assert (
        await coordinator.clarify(receipt.case_id, answer(message="other-answer"))
    ).status == "stale"
    await until(lambda: coordinator.active is None)
    assert (
        len(
            [
                event
                for event in coordinator.records[receipt.case_id].events
                if isinstance(event, Clarification)
            ]
        )
        == 1
    )
    assert services.trace == ["investigate", "investigate", "team_report"]
    first = await coordinator.public_event(
        receipt.case_id, PublicEventQuery(source_thread_id="client-thread")
    )
    assert isinstance(first, Clarification)
    second = await coordinator.public_event(
        receipt.case_id,
        PublicEventQuery(source_thread_id="client-thread", after=first.cursor),
    )
    assert isinstance(second, HandedOff)
    assert isinstance(
        await coordinator.public_event(
            receipt.case_id,
            PublicEventQuery(source_thread_id="client-thread", after=second.cursor),
        ),
        Pending,
    )


async def test_early_answer_waits_for_matching_interrupt(
    services: FakeServices,
) -> None:
    from tech_support_agent.bug_resolution.graph import build_bug_graph

    coordinator = CaseCoordinator(services.dependencies())
    published = asyncio.Event()
    let_graph_pause = asyncio.Event()

    async def publish(case_id: str, event: Clarification | HandedOff) -> None:
        await coordinator.publish(case_id, event)
        if isinstance(event, Clarification):
            published.set()
            await let_graph_pause.wait()

    coordinator.graph = build_bug_graph(services.dependencies(), publish)
    try:
        receipt = await coordinator.accept_case(report())
        await asyncio.wait_for(published.wait(), 3)
        assert (
            await coordinator.clarify(receipt.case_id, answer())
        ).status == "accepted"
        assert services.trace == ["investigate"]
        assert (await coordinator.accept_case(report("second"))).status == "busy"
        let_graph_pause.set()
        await until(lambda: coordinator.active is None)
        assert services.trace == ["investigate", "investigate", "team_report"]
        assert services.cases[-1].clarification.answer == "Grade 3"
    finally:
        await coordinator.close()


@pytest.mark.parametrize("raises", [False, True])
async def test_delivery_failure_is_private_and_releases_slot(
    coordinator: CaseCoordinator,
    services: FakeServices,
    raises: bool,
    caplog: pytest.LogCaptureFixture,
) -> None:
    services.question = None
    services.fail_delivery = raises
    services.delivery_ack = False
    receipt = await coordinator.accept_case(report())
    await until(lambda: coordinator.active is None)
    with pytest.raises(CaseUnavailable):
        await coordinator.public_event(
            receipt.case_id, PublicEventQuery(source_thread_id="client-thread")
        )
    assert not coordinator.records[receipt.case_id].events
    assert PRIVATE_SENTINEL not in caplog.text and TOKEN not in caplog.text
    assert (await coordinator.accept_case(report("next"))).status == "accepted"


async def test_runtime_dependencies_fail_closed() -> None:
    coordinator = CaseCoordinator(
        IntakeServices(unavailable_investigator, unavailable_delivery)
    )
    try:
        receipt = await coordinator.accept_case(report())
        await until(lambda: coordinator.active is None)
        assert coordinator.records[receipt.case_id].failed
        assert not coordinator.records[receipt.case_id].events
    finally:
        await coordinator.close()


async def test_http_auth_thread_guards_and_no_resume_surface(
    api: PrivateAPI, coordinator: CaseCoordinator
) -> None:
    url = api.settings.worker_url
    async with ClientSession() as session:
        for path in ("/cases", "/history", "/cases/unknown/resume"):
            async with session.post(
                f"{url}{path}", json=report().model_dump()
            ) as response:
                assert response.status == 401
        receipt = await api.accept_case(report())
        headers = {"Authorization": f"Bearer {TOKEN}"}
        async with session.get(
            f"{url}/cases/{receipt.case_id}/public-event",
            headers=headers,
            params={"source_thread_id": "wrong"},
        ) as response:
            assert response.status == 404
        async with session.post(
            f"{url}/cases/{receipt.case_id}/clarification",
            headers=headers,
            json=answer(thread="wrong").model_dump(),
        ) as response:
            assert response.status == 404
        for path in (
            "/history",
            "/state",
            "/approval",
            f"/cases/{receipt.case_id}/resume",
        ):
            async with session.post(
                f"{url}{path}", headers=headers, json={"resume": "approved"}
            ) as response:
                assert response.status == 404
                assert "Access-Control-Allow-Origin" not in response.headers
        for payload in (
            {**report().model_dump(), "command": {"resume": "approve"}},
            {
                **report().model_dump(),
                "attachments": [
                    {"type": "file", "url": "https://example.com/file.pdf"}
                ],
            },
            {
                **report().model_dump(),
                "attachments": [
                    {"type": "image", "mime_type": "image/png", "data": "invalid"}
                ],
            },
        ):
            async with session.post(
                f"{url}/cases", headers=headers, json=payload
            ) as response:
                assert response.status == 400
                assert await response.json() == {"error": "invalid_request"}
        async with session.post(
            f"{url}/cases",
            headers=headers,
            data=b"x" * (MAX_BODY_BYTES + 1),
            skip_auto_headers={"Content-Type"},
        ) as response:
            assert response.status == 400
        async with session.post(
            f"{url}/cases",
            headers={**headers, "Content-Type": "application/json"},
            data=b"x" * (MAX_BODY_BYTES + 1),
        ) as response:
            assert response.status == 413
    assert len(coordinator.records) == 1


def test_attachment_validation_and_no_partial_case() -> None:
    output = BytesIO()
    Image.new("RGB", (2, 2)).save(output, format="PNG")
    data = base64.b64encode(output.getvalue()).decode()
    image = ImageAttachment(mime_type="image/png", data=data)
    assert CaseRequest(
        **{**report().model_dump(), "attachments": [image]}
    ).attachments == [image]
    for mime, content in (
        ("image/jpeg", data),
        ("image/png", "bm90LWFuLWltYWdl"),
        ("image/png", data[:32]),
    ):
        with pytest.raises(ValidationError):
            ImageAttachment(mime_type=mime, data=content)
    with pytest.raises(ValidationError):
        CaseRequest(**{**report().model_dump(), "attachments": [image] * 4})


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com",
        "http://localhost:2025",
        "http://127.0.0.1:2025/path",
        "http://user:password@127.0.0.1:2025",
    ],
)
def test_only_literal_loopback_worker_url(url: str) -> None:
    with pytest.raises(ValidationError):
        BugSettings(_env_file=None, worker_token=TOKEN, worker_url=url)


async def test_shutdown_cancels_wait_and_refuses_new_work(
    coordinator: CaseCoordinator,
) -> None:
    receipt = await coordinator.accept_case(report())
    await until(lambda: coordinator.records[receipt.case_id].events)
    await coordinator.close()
    assert coordinator.active is None
    assert coordinator.records[receipt.case_id].task.done()
    with pytest.raises(CaseUnavailable):
        await coordinator.accept_case(report("next"))


async def test_distinct_concurrent_threads_and_delivery_hold_slot(
    coordinator: CaseCoordinator, services: FakeServices
) -> None:
    services.question = None
    services.delivery_gate = asyncio.Event()
    receipts = await asyncio.gather(
        *(coordinator.accept_case(report(f"thread-{i}")) for i in range(10))
    )
    assert [item.status for item in receipts].count("accepted") == 1
    assert [item.status for item in receipts].count("busy") == 9
    await until(lambda: services.trace == ["investigate", "team_report"])
    assert len(coordinator.records) == 1
    record = next(iter(coordinator.records.values()))
    assert not record.events and not record.task.done()
    assert (await coordinator.accept_case(report("while-delivering"))).status == "busy"
    services.delivery_gate.set()
    await until(lambda: coordinator.active is None)
    assert record.task.done() and record.terminal
    assert (
        await coordinator.accept_case(report("after-delivery"))
    ).status == "accepted"


async def test_http_rejects_invalid_before_allocating_and_authenticates_all_routes(
    api: PrivateAPI, coordinator: CaseCoordinator
) -> None:
    url = api.settings.worker_url
    headers = {"Authorization": f"Bearer {TOKEN}"}
    async with ClientSession() as session:
        for method, path in (
            ("POST", "/cases"),
            ("GET", "/cases/unknown/public-event"),
            ("POST", "/cases/unknown/clarification"),
        ):
            async with session.request(
                method, f"{url}{path}", headers={"Authorization": "Bearer incorrect"}
            ) as response:
                assert response.status == 401
        for payload in (
            {**report().model_dump(), "report": "  "},
            {**report().model_dump(), "resume": "approve"},
            {
                **report().model_dump(),
                "attachments": [
                    {"type": "image", "url": "https://example.com/image.png"}
                ],
            },
        ):
            async with session.post(
                f"{url}/cases", headers=headers, json=payload
            ) as response:
                assert response.status == 400
            assert not coordinator.records and coordinator.active is None
        receipt = await api.accept_case(report())
        for query in (
            "",
            "source_thread_id=client-thread&after=-1",
            "source_thread_id=client-thread&resume=approve",
            "source_thread_id=client-thread&source_thread_id=wrong",
        ):
            async with session.get(
                f"{url}/cases/{receipt.case_id}/public-event?{query}", headers=headers
            ) as response:
                assert response.status == 400
        await until(lambda: coordinator.records[receipt.case_id].events)
        for payload in (
            {**answer().model_dump(), "resume": "approve"},
            {
                key: value
                for key, value in answer().model_dump().items()
                if key != "clarification_cursor"
            },
        ):
            async with session.post(
                f"{url}/cases/{receipt.case_id}/clarification",
                headers=headers,
                json=payload,
            ) as response:
                assert response.status == 400
        assert not coordinator.records[receipt.case_id].answers


def test_delivery_requires_an_actual_boolean_acknowledgement() -> None:
    for value in ("true", "false", 1, None):
        with pytest.raises(ValidationError):
            DeliveryReceipt(delivered=value)


async def test_shutdown_before_execution_starts_is_not_pending(
    coordinator: CaseCoordinator,
) -> None:
    receipt = await coordinator.accept_case(report())
    await coordinator.close()
    assert coordinator.active is None
    assert coordinator.records[receipt.case_id].task.done()
    with pytest.raises(CaseUnavailable):
        await coordinator.public_event(
            receipt.case_id, PublicEventQuery(source_thread_id="client-thread")
        )
