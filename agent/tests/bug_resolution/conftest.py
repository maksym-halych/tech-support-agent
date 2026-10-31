import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from itertools import count

import pytest
from aiohttp.test_utils import TestServer

from tech_support_agent.bug_resolution.adapters.private_api import PrivateAPI
from tech_support_agent.bug_resolution.contracts import (
    CaseRequest,
    CaseSnapshot,
    DeliveryReceipt,
    IntakeResult,
)
from tech_support_agent.bug_resolution.coordinator import CaseCoordinator
from tech_support_agent.bug_resolution.graph import IntakeServices
from tech_support_agent.bug_resolution.worker import create_app
from tech_support_agent.config import BugSettings

PRIVATE_SENTINEL = "PRIVATE-DIAGNOSIS-DO-NOT-EXPOSE"
TOKEN = "test-only-worker-token-0123456789abcdef"


@dataclass
class FakeServices:
    question: str | None = "Which grade did you select?"
    fail_delivery: bool = False
    delivery_ack: bool = True
    investigate_gate: asyncio.Event | None = None
    delivery_gate: asyncio.Event | None = None
    trace: list[str] = field(default_factory=list)
    cases: list[CaseSnapshot] = field(default_factory=list)

    async def investigate(self, case: CaseSnapshot) -> IntakeResult:
        self.trace.append("investigate")
        self.cases.append(case)
        if self.investigate_gate:
            await self.investigate_gate.wait()
        return IntakeResult(question=self.question, private_summary=PRIVATE_SENTINEL)

    async def deliver(self, case: CaseSnapshot) -> DeliveryReceipt:
        self.trace.append("team_report")
        if self.delivery_gate:
            await self.delivery_gate.wait()
        if self.fail_delivery:
            raise RuntimeError(f"{PRIVATE_SENTINEL} {TOKEN}")
        return DeliveryReceipt(delivered=self.delivery_ack)

    def dependencies(self) -> IntakeServices:
        return IntakeServices(self.investigate, self.deliver)


@pytest.fixture
def services() -> FakeServices:
    return FakeServices()


@pytest.fixture
async def coordinator(services: FakeServices) -> AsyncIterator[CaseCoordinator]:
    ids = count()
    coordinator = CaseCoordinator(
        services.dependencies(), id_source=lambda: f"test-{next(ids)}"
    )
    yield coordinator
    await coordinator.close()


@pytest.fixture
async def api(coordinator: CaseCoordinator) -> AsyncIterator[PrivateAPI]:
    settings = BugSettings(
        _env_file=None, worker_token=TOKEN, poll_timeout=0.2, poll_interval=0.005
    )
    async with TestServer(
        create_app(settings, coordinator), host="127.0.0.1"
    ) as server:
        yield PrivateAPI(
            settings.model_copy(
                update={"worker_url": str(server.make_url("")).rstrip("/")}
            )
        )


def report(thread: str = "client-thread", message: str = "report-1") -> CaseRequest:
    return CaseRequest(
        source_thread_id=thread,
        source_message_id=message,
        report="Continue is disabled",
    )


async def until(predicate: Callable[[], object], timeout: float = 3) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.005)


@dataclass
class FakeClock:
    now: float = 0

    def __call__(self) -> float:
        return self.now

    async def sleep(self, delay: float) -> None:
        self.now += delay
        await asyncio.sleep(0)
