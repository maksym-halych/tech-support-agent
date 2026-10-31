"""Worker-run admission, thread binding and a single-invocation mailbox."""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from uuid import uuid4

from langchain_core.runnables import RunnableConfig
from langgraph.types import Command
from langsmith import tracing_context

from tech_support_agent.bug_resolution.contracts import (
    CaseReceipt,
    CaseRequest,
    CaseSnapshot,
    Clarification,
    ClarificationRequest,
    ClientWait,
    EventReceipt,
    HandedOff,
    Pending,
    PublicEvent,
    PublicEventQuery,
)
from tech_support_agent.bug_resolution.graph import IntakeServices, build_bug_graph
from tech_support_agent.bug_resolution.nodes.intake import BugState

logger = logging.getLogger(__name__)


class CaseNotFound(Exception):
    """Unknown case or wrong source thread; intentionally indistinguishable."""


class CaseUnavailable(Exception):
    """Private execution failed; no private reason belongs in an HTTP response."""


@dataclass
class CaseRecord:
    """Process-local bookkeeping, never exposed or checkpointed publicly."""

    initial: CaseSnapshot
    events: list[Clarification | HandedOff] = field(default_factory=list)
    mailbox: ClarificationRequest | None = None
    wake: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task[None] | None = None
    answers: set[tuple[str, int]] = field(default_factory=set)
    failed: bool = False
    terminal: bool = False


class CaseCoordinator:
    """Own one active slot, including waits, and retain terminal thread bindings."""

    def __init__(
        self,
        services: IntakeServices,
        id_source: Callable[[], str] = lambda: str(uuid4()),
    ) -> None:
        self.id_source = id_source
        self.run_id = id_source()
        self.records: dict[str, CaseRecord] = {}
        self.bindings: dict[str, str] = {}
        self.submissions: dict[tuple[str, str], str] = {}
        self.active: str | None = None
        self.lock = asyncio.Lock()
        self.closed = False
        self.graph = build_bug_graph(services, self.publish)

    async def accept_case(self, request: CaseRequest) -> CaseReceipt:
        """Atomically admit or reattach without holding a lock over graph work."""

        async with self.lock:
            if self.closed:
                raise CaseUnavailable()
            key = (request.source_thread_id, request.source_message_id)
            existing = self.submissions.get(key) or self.bindings.get(
                request.source_thread_id
            )
            if existing:
                self.submissions[key] = existing
                return CaseReceipt(status="duplicate", case_id=existing)
            if self.active:
                return CaseReceipt(status="busy")
            case_id = self.id_source()
            record = CaseRecord(
                CaseSnapshot(run_id=self.run_id, case_id=case_id, request=request)
            )
            self.records[case_id] = record
            self.bindings[request.source_thread_id] = case_id
            self.submissions[key] = case_id
            self.active = case_id
            record.task = asyncio.create_task(
                self._execute(record), name=f"case-{case_id}"
            )
            record.task.add_done_callback(lambda task: self._release(case_id))
            return CaseReceipt(status="accepted", case_id=case_id)

    def _release(self, case_id: str) -> None:
        record = self.records[case_id]
        record.terminal = True
        if record.task is not None and record.task.cancelled():
            record.failed = True
        if self.active == case_id:
            self.active = None

    def _bound_record(self, case_id: str, thread_id: str) -> CaseRecord:
        record = self.records.get(case_id)
        if record is None or record.initial.request.source_thread_id != thread_id:
            raise CaseNotFound()
        return record

    async def public_event(self, case_id: str, query: PublicEventQuery) -> PublicEvent:
        """Read the first undelivered event, never skip ahead to the latest."""

        async with self.lock:
            record = self._bound_record(case_id, query.source_thread_id)
            if record.failed:
                raise CaseUnavailable()
            return next(
                (event for event in record.events if event.cursor > query.after),
                Pending(),
            )

    async def clarify(
        self, case_id: str, request: ClarificationRequest
    ) -> EventReceipt:
        """Queue one answer, including during the send-to-interrupt transition."""

        async with self.lock:
            record = self._bound_record(case_id, request.source_thread_id)
            key = (request.source_message_id, request.clarification_cursor)
            if key in record.answers:
                return EventReceipt(status="duplicate")
            question = record.events[-1] if record.events else None
            if (
                record.terminal
                or record.failed
                or record.answers
                or not isinstance(question, Clarification)
                or request.clarification_cursor != question.cursor
                or (request.source_thread_id, request.source_message_id)
                in self.submissions
            ):
                return EventReceipt(status="stale")
            record.answers.add(key)
            record.mailbox = request
            record.wake.set()
            return EventReceipt(status="accepted")

    async def publish(self, case_id: str, event: Clarification | HandedOff) -> None:
        """Record only explicitly allowed public events in monotonically ordered form."""

        async with self.lock:
            record = self.records[case_id]
            if record.events and record.events[-1].cursor >= event.cursor:
                raise ValueError("Duplicate or out-of-order private event")
            record.events.append(event)

    async def _execute(self, record: CaseRecord) -> None:
        config: RunnableConfig = {"configurable": {"thread_id": record.initial.case_id}}
        value: BugState | Command = {"case": record.initial}
        try:
            # Never inherit public callbacks or ship private checkpoints to tracing.
            with tracing_context(enabled=False):
                while True:
                    await self.graph.ainvoke(value, config)
                    snapshot = await self.graph.aget_state(config)
                    if not snapshot.next:
                        return
                    interrupts = [
                        item for task in snapshot.tasks for item in task.interrupts
                    ]
                    if len(interrupts) != 1:
                        raise ValueError("Unexpected private wait")
                    wait = ClientWait.model_validate(interrupts[0].value)
                    await record.wake.wait()
                    async with self.lock:
                        answer = record.mailbox
                        record.mailbox = None
                        record.wake.clear()
                    if (
                        answer is None
                        or wait.case_id != record.initial.case_id
                        or answer.clarification_cursor != wait.clarification_cursor
                    ):
                        raise ValueError("Mismatched mailbox event")
                    value = Command(resume=answer.model_dump())
        except asyncio.CancelledError:
            record.failed = True
            raise
        except Exception:  # noqa: BLE001 - contain arbitrary adapter failures privately
            record.failed = True
            logger.error(
                f"Private case execution failed; case_id={record.initial.case_id}"
            )

    async def close(self) -> None:
        """Cancel owned executions and wait for them before releasing resources."""

        self.closed = True
        tasks = [
            record.task for record in self.records.values() if record.task is not None
        ]
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
