"""Worker-run admission, thread binding and a single-invocation mailbox."""

import asyncio
import logging
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from uuid import uuid4

from langchain_core.runnables import RunnableConfig
from langgraph.types import Command
from langsmith import tracing_context

from tech_support_agent.bug_resolution.contracts import (
    ApprovedPlan,
    CaseReceipt,
    CaseRequest,
    CaseSnapshot,
    Clarification,
    ClarificationRequest,
    ClientWait,
    EventReceipt,
    HandedOff,
    Pending,
    PublicationWait,
    PublicEvent,
    PublicEventQuery,
    TeamButton,
    TeamCallback,
    TeamDelivery,
    TeamEvent,
    TeamMessage,
    TeamReply,
    TeamWait,
)
from tech_support_agent.bug_resolution.graph import (
    IntakeServices,
    Phase3Services,
    build_bug_graph,
)
from tech_support_agent.bug_resolution.nodes.intake import BugState
from tech_support_agent.bug_resolution.publication_contracts import (
    CIObservation,
    CIWait,
    PublicationAuthority,
    PublicationRef,
)

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
    mailbox: ClarificationRequest | TeamEvent | CIObservation | None = None
    wake: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task[None] | None = None
    answers: set[tuple[str, int]] = field(default_factory=set)
    failed: bool = False
    terminal: bool = False
    team_wait: TeamWait | None = None
    delivery_ready: asyncio.Event = field(default_factory=asyncio.Event)
    final: CaseSnapshot | None = None
    decision_case: CaseSnapshot | None = None
    approved: ApprovedPlan | None = None
    ci_wait: CIWait | None = None


@dataclass(frozen=True)
class DecisionToken:
    """Process-local token binding, never supplied by a model or the public bridge."""

    run_id: str
    case_id: str
    revision: int
    action: str


class CaseCoordinator:
    """Own one active slot, including waits, and retain terminal thread bindings."""

    def __init__(
        self,
        services: IntakeServices | Phase3Services,
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
        self.services = services
        self.tokens: dict[str, DecisionToken] = {}
        self.updates: set[int] = set()
        self.callbacks: set[str] = set()
        self.graph = build_bug_graph(
            services,
            self.publish,
            self.send_team,
            self.publication_authority,
            self.arm_ci,
        )

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
                CaseSnapshot(
                    run_id=self.run_id,
                    case_id=case_id,
                    request=request,
                    scenario=self.services.scenario
                    if isinstance(self.services, Phase3Services)
                    else "continue",
                    base_sha=self.services.base_sha
                    if isinstance(self.services, Phase3Services)
                    else None,
                )
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
        self._invalidate_tokens(case_id)
        record.team_wait = None
        record.ci_wait = None
        record.delivery_ready.set()
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
            if record.failed and not any(
                isinstance(event, HandedOff) for event in record.events
            ):
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

    def _invalidate_tokens(self, case_id: str) -> None:
        self.tokens = {
            token: binding
            for token, binding in self.tokens.items()
            if binding.case_id != case_id
        }

    async def send_team(
        self, case: CaseSnapshot, message: TeamMessage, mode: str
    ) -> TeamWait:
        """Invalidate old buttons, send outside the lock, then bind the actual message ID."""

        if not isinstance(self.services, Phase3Services):
            raise TypeError("No team transport configured")
        record = self.records[case.case_id]
        buttons = []
        async with self.lock:
            self._invalidate_tokens(case.case_id)
            record.team_wait = None
            record.approved = None
            record.decision_case = case
            record.delivery_ready.clear()
            for action, label in (
                ("approve", "Approve fix"),
                ("discuss", "Discuss"),
                ("reject", "Reject"),
            ):
                if mode == "discussion" and action != "reject":
                    continue
                token = secrets.token_urlsafe(24)
                self.tokens[token] = DecisionToken(
                    self.run_id, case.case_id, case.plan_revision, action
                )
                buttons.append(TeamButton(text=label, token=token))
        try:
            delivery = TeamDelivery.model_validate(
                await self.services.send(
                    message.model_copy(update={"buttons": buttons})
                )
            )
            wait = TeamWait(
                run_id=self.run_id,
                case_id=case.case_id,
                plan_revision=case.plan_revision,
                message_id=delivery.message_id,
                mode=mode,
            )
            async with self.lock:
                record.team_wait = wait
            return wait
        finally:
            record.delivery_ready.set()

    def _authorized_team(self, event: TeamCallback | TeamReply) -> bool:
        return bool(
            isinstance(self.services, Phase3Services)
            and event.chat_type == "private"
            and event.user_id == self.services.telegram_user_id
            and event.chat_id == self.services.telegram_chat_id
            and not self.closed
        )

    async def consume_callback(self, event: TeamCallback) -> EventReceipt:
        """Atomically consume the current capability once, including send-to-wait races."""

        if not self._authorized_team(event):
            return EventReceipt(status="stale")
        # HTTP delivery can complete after Telegram has exposed its buttons. The
        # poller already acknowledged the click; wait only for that send's receipt.
        binding = self.tokens.get(event.token)
        if binding and binding.case_id in self.records:
            try:
                async with asyncio.timeout(20):
                    await self.records[binding.case_id].delivery_ready.wait()
            except TimeoutError:
                return EventReceipt(status="stale")
        async with self.lock:
            if event.update_id in self.updates or event.callback_id in self.callbacks:
                return EventReceipt(status="duplicate")
            self.updates.add(event.update_id)
            self.callbacks.add(event.callback_id)
            binding = self.tokens.get(event.token)
            if (
                binding is None
                or binding.run_id != self.run_id
                or self.active != binding.case_id
            ):
                return EventReceipt(status="stale")
            record = self.records[binding.case_id]
            wait = record.team_wait
            if (
                record.failed
                or record.terminal
                or record.mailbox is not None
                or wait is None
                or wait.plan_revision != binding.revision
                or wait.message_id != event.message_id
                or (wait.mode == "discussion" and binding.action != "reject")
            ):
                return EventReceipt(status="stale")
            record.mailbox = TeamEvent(
                wait=wait, action=binding.action, user_id=event.user_id
            )
            if binding.action == "approve":
                plan = record.decision_case.plan
                record.approved = ApprovedPlan(
                    revision=binding.revision,
                    expected_behavior=plan.expected_behavior,
                    allowed_changes=plan.allowed_changes,
                    acceptance_checks=plan.acceptance_checks,
                    approved_by=event.user_id,
                )
            self._invalidate_tokens(binding.case_id)
            record.team_wait = None
            record.wake.set()
            return EventReceipt(status="accepted")

    async def consume_reply(self, event: TeamReply) -> EventReceipt:
        """Accept only an authorized reply to the active discussion message, never approval."""

        if not self._authorized_team(event):
            return EventReceipt(status="stale")
        async with self.lock:
            if event.update_id in self.updates:
                return EventReceipt(status="duplicate")
            self.updates.add(event.update_id)
            record = self.records.get(self.active)
            wait = record.team_wait if record else None
            if (
                record is None
                or record.failed
                or record.terminal
                or record.mailbox is not None
                or wait is None
                or wait.mode != "discussion"
                or wait.message_id != event.reply_to_message_id
            ):
                return EventReceipt(status="stale")
            record.mailbox = TeamEvent(
                wait=wait, action="reply", user_id=event.user_id, text=event.text
            )
            self._invalidate_tokens(record.initial.case_id)
            record.team_wait = None
            record.wake.set()
            return EventReceipt(status="accepted")

    async def publication_authority(self, case: CaseSnapshot) -> PublicationAuthority:
        """Capture authority under the lock from the genuinely consumed private decision."""

        async with self.lock:
            record = self.records.get(case.case_id)
            if (
                record is None
                or self.active != case.case_id
                or record.terminal
                or record.failed
                or record.approved is None
                or record.approved != case.approval
                or case.run_id != self.run_id
                or case.phase not in ("verified", "waiting_ci")
                or case.investigation is None
                or case.investigation.status != "confirmed"
            ):
                raise ValueError(
                    "Current coordinator approval required for publication"
                )
            return PublicationAuthority(
                run_id=self.run_id,
                case_id=case.case_id,
                attempt_count=case.attempt_count,
                plan_revision=case.plan_revision,
                approved_revision=record.approved.revision,
                approved_by=record.approved.approved_by,
                investigation="confirmed",
                phase=case.phase,
            )

    async def arm_ci(self, publication: PublicationRef) -> None:
        """Arm the full wait identity before notification/checkpoint so early events queue safely."""

        async with self.lock:
            record = self.records.get(publication.candidate.case_id)
            if (
                record is None
                or publication.run_id != self.run_id
                or self.active != publication.candidate.case_id
                or record.terminal
                or record.failed
            ):
                raise ValueError("Publication does not belong to the active case")
            record.ci_wait = CIWait(publication=publication)

    async def consume_ci(self, observation: CIObservation) -> EventReceipt:
        """Accept trusted poller observations only for the entire currently armed publication."""

        observation = CIObservation.model_validate(observation)
        async with self.lock:
            record = self.records.get(observation.publication.candidate.case_id)
            if (
                self.closed
                or record is None
                or record.terminal
                or record.failed
                or record.ci_wait is None
                or record.ci_wait.publication != observation.publication
            ):
                return EventReceipt(status="stale")
            if record.mailbox is not None:
                return EventReceipt(
                    status="duplicate" if record.mailbox == observation else "stale"
                )
            if observation.status == "pending":
                return EventReceipt(status="accepted")
            if observation.status in ("passed", "failed") and (
                observation.run_id is None or observation.run_attempt is None
            ):
                return EventReceipt(status="stale")
            record.mailbox = observation
            record.wake.set()
            return EventReceipt(status="accepted")

    async def _poll_ci(self, wait: CIWait) -> None:
        workflow = self.services.workflow
        while True:
            try:
                async with asyncio.timeout(180):
                    observation = await workflow.inspect_ci(wait.publication)
            except Exception:  # noqa: BLE001 - observation failures cannot imply success
                observation = CIObservation(
                    publication=wait.publication,
                    status="pending",
                    reason="CI observation unavailable; no success claimed",
                )
            receipt = await self.consume_ci(observation)
            if receipt.status == "accepted" and observation.status != "pending":
                return
            await asyncio.sleep(workflow.poll_interval)

    async def _execute(self, record: CaseRecord) -> None:
        config: RunnableConfig = {
            "configurable": {"thread_id": record.initial.case_id},
            "recursion_limit": 200,
        }
        value: BugState | Command = {"case": record.initial}
        try:
            # Never inherit public callbacks or ship private checkpoints to tracing.
            with tracing_context(enabled=False):
                while True:
                    await self.graph.ainvoke(value, config)
                    snapshot = await self.graph.aget_state(config)
                    record.final = CaseSnapshot.model_validate(snapshot.values["case"])
                    if not snapshot.next:
                        return
                    interrupts = [
                        item for task in snapshot.tasks for item in task.interrupts
                    ]
                    if len(interrupts) != 1:
                        raise ValueError("Unexpected private wait")
                    raw_wait = interrupts[0].value
                    if raw_wait.get("kind") == "publication":
                        wait = PublicationWait.model_validate(raw_wait)
                        if (wait.run_id, wait.case_id, wait.attempt, wait.revision) != (
                            self.run_id,
                            record.initial.case_id,
                            record.final.attempt_count,
                            record.final.plan_revision,
                        ):
                            raise ValueError("Mismatched publication wait")
                        await asyncio.sleep(self.services.workflow.poll_interval)
                        value = Command(resume=wait.model_dump())
                        continue
                    if "publication" in raw_wait:
                        wait = CIWait.model_validate(raw_wait)
                        if record.ci_wait != wait:
                            raise ValueError("CI wait was not armed by publication")
                        poller = asyncio.create_task(self._poll_ci(wait))
                        try:
                            await record.wake.wait()
                        finally:
                            poller.cancel()
                            await asyncio.gather(poller, return_exceptions=True)
                        async with self.lock:
                            observation = record.mailbox
                            record.mailbox = None
                            record.ci_wait = None
                            record.wake.clear()
                        if (
                            not isinstance(observation, CIObservation)
                            or observation.publication != wait.publication
                        ):
                            raise ValueError("Mismatched CI mailbox event")
                        value = Command(resume=observation.model_dump())
                        continue
                    wait = (
                        TeamWait.model_validate(raw_wait)
                        if raw_wait.get("kind") == "team"
                        else ClientWait.model_validate(raw_wait)
                    )
                    await record.wake.wait()
                    async with self.lock:
                        answer = record.mailbox
                        record.mailbox = None
                        record.wake.clear()
                    if (
                        answer is None
                        or wait.case_id != record.initial.case_id
                        or (
                            isinstance(wait, ClientWait)
                            and (
                                not isinstance(answer, ClarificationRequest)
                                or answer.clarification_cursor
                                != wait.clarification_cursor
                            )
                        )
                        or (
                            isinstance(wait, TeamWait)
                            and (
                                not isinstance(answer, TeamEvent) or answer.wait != wait
                            )
                        )
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
        finally:
            if isinstance(self.services, Phase3Services) and self.services.cleanup:
                try:
                    await self.services.cleanup(record.initial.case_id)
                except Exception:  # noqa: BLE001 - contain cleanup failures privately
                    record.failed = True
                    self.closed = True
                    logger.error("Private case resource cleanup failed")

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
