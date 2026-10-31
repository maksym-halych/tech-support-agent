"""A private graph: investigate, optionally clarify once, deliver, finish."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.pregel import Pregel
from langgraph.types import interrupt

from tech_support_agent.bug_resolution.contracts import (
    CaseSnapshot,
    Clarification,
    DeliveryReceipt,
    ExecutionOutcome,
    HandedOff,
    IntakeResult,
    InvestigationResult,
    PlanProposal,
    PublicationWait,
    TeamDelivery,
    TeamMessage,
    TeamWait,
)
from tech_support_agent.bug_resolution.nodes.intake import BugState, wait_for_client
from tech_support_agent.bug_resolution.nodes.plan import plan_message, version_plan
from tech_support_agent.bug_resolution.nodes.wait import wait_for_team
from tech_support_agent.bug_resolution.orchestration import ResolutionWorkflow, terminal
from tech_support_agent.bug_resolution.publication_contracts import (
    CIObservation,
    CIWait,
    PublicationAuthority,
    PublicationRef,
)

type PublishEvent = Callable[[str, Clarification | HandedOff], Awaitable[None]]


@dataclass(frozen=True)
class IntakeServices:
    """Private dependencies; tests inject doubles, runtime never simulates success."""

    investigate: Callable[[CaseSnapshot], Awaitable[IntakeResult]]
    deliver: Callable[[CaseSnapshot], Awaitable[DeliveryReceipt]]


@dataclass(frozen=True)
class Phase3Services:
    """Typed runtime seam; downstream code receives only a genuinely approved case."""

    investigate: Callable[[CaseSnapshot], Awaitable[InvestigationResult]]
    propose: Callable[[CaseSnapshot], Awaitable[PlanProposal]]
    send: Callable[[TeamMessage], Awaitable[TeamDelivery]]
    telegram_user_id: int
    telegram_chat_id: int
    scenario: Literal["continue", "both"] = "continue"
    base_sha: str | None = None
    execute_approved: Callable[[CaseSnapshot], Awaitable[ExecutionOutcome]] | None = (
        None
    )
    cleanup: Callable[[str], Awaitable[None]] | None = None
    workflow: ResolutionWorkflow | None = None


type SendTeam = Callable[
    [CaseSnapshot, TeamMessage, Literal["decision", "discussion"]], Awaitable[TeamWait]
]


async def unavailable_investigator(case: CaseSnapshot) -> IntakeResult:
    """Fail closed until a real investigator is wired in a later phase."""

    raise RuntimeError("Investigator is not configured")


async def unavailable_delivery(case: CaseSnapshot) -> DeliveryReceipt:
    """Fail closed until a real team delivery service is wired."""

    raise RuntimeError("Team delivery is not configured")


def build_bug_graph(
    services: IntakeServices | Phase3Services,
    publish: PublishEvent,
    send_team: SendTeam | None = None,
    authority: Callable[[CaseSnapshot], Awaitable[PublicationAuthority]] | None = None,
    arm_ci: Callable[[PublicationRef], Awaitable[None]] | None = None,
) -> Pregel:
    """Compile the private graph with its own in-memory checkpoints.

    Args:
        services: Investigator and team delivery dependencies.
        publish: Coordinator-owned safe event sink.
    Returns:
        A graph that must never be registered on Agent Server.
    """

    if isinstance(services, Phase3Services):
        if send_team is None:
            raise ValueError("Phase 3 requires the coordinator-owned decision gate")
        return build_phase3_graph(services, publish, send_team, authority, arm_ci)

    async def investigate(state: BugState) -> BugState:
        case = CaseSnapshot.model_validate(state["case"])
        result = IntakeResult.model_validate(await services.investigate(case))
        return {"case": case.model_copy(update={"result": result})}

    def next_step(state: BugState) -> str:
        case = state["case"]
        if case.result is None:
            raise ValueError("Missing investigation result")
        return (
            "request_clarification"
            if case.result.question and case.clarification is None
            else "team_report"
        )

    async def request_clarification(state: BugState) -> BugState:
        case = state["case"]
        if case.result is None or case.result.question is None:
            raise ValueError("Missing clarification question")
        await publish(
            case.case_id, Clarification(question=case.result.question, cursor=1)
        )
        return {"case": case.model_copy(update={"clarification_cursor": 1})}

    async def team_report(state: BugState) -> BugState:
        case = state["case"]
        receipt = DeliveryReceipt.model_validate(await services.deliver(case))
        if not receipt.delivered:
            raise RuntimeError("Team delivery not acknowledged")
        await publish(case.case_id, HandedOff(cursor=2 if case.clarification else 1))
        return {"case": case}

    graph = StateGraph(BugState)
    graph.add_node("investigate", investigate)
    graph.add_node("request_clarification", request_clarification)
    graph.add_node("wait_for_client", wait_for_client)
    graph.add_node("team_report", team_report)
    graph.add_edge(START, "investigate")
    graph.add_conditional_edges(
        "investigate", next_step, ["request_clarification", "team_report"]
    )
    graph.add_edge("request_clarification", "wait_for_client")
    graph.add_edge("wait_for_client", "investigate")
    graph.add_edge("team_report", END)
    return graph.compile(checkpointer=InMemorySaver())


def build_phase3_graph(
    services: Phase3Services,
    publish: PublishEvent,
    send_team: SendTeam,
    authority: Callable[[CaseSnapshot], Awaitable[PublicationAuthority]] | None = None,
    arm_ci: Callable[[PublicationRef], Awaitable[None]] | None = None,
) -> Pregel:
    """Compile explicit diagnosis, planning, delivery, wait and approved handoff stages."""

    async def investigate(state: BugState) -> BugState:
        case = CaseSnapshot.model_validate(state["case"])
        try:
            result = InvestigationResult.model_validate(
                await services.investigate(case)
            )
        except Exception:  # noqa: BLE001 - infrastructure failures are never absent bugs
            result = InvestigationResult(
                status="blocked",
                summary="Investigation blocked by a model, tool, or execution failure.",
            )
        return {"case": case.model_copy(update={"investigation": result})}

    def diagnosis_route(state: BugState) -> str:
        case = state["case"]
        result = case.investigation
        if result.status == "confirmed":
            return "propose_plan"
        if (
            result.status in ("needs_details", "unconfirmed")
            and case.clarification is None
        ):
            return "request_clarification"
        return "report_diagnosis"

    async def clarify(state: BugState) -> BugState:
        case = state["case"]
        question = (
            case.investigation.question
            or "What steps and selected values reproduce the problem?"
        )
        await publish(case.case_id, Clarification(question=question, cursor=1))
        return {"case": case.model_copy(update={"clarification_cursor": 1})}

    async def handoff(case: CaseSnapshot) -> CaseSnapshot:
        if not case.handed_off:
            await publish(
                case.case_id, HandedOff(cursor=2 if case.clarification else 1)
            )
        return case.model_copy(update={"handed_off": True})

    async def report_diagnosis(state: BugState) -> BugState:
        case = state["case"]
        await services.send(
            TeamMessage(
                text=(
                    f"Case {case.case_id}: {case.investigation.status}\n"
                    f"{case.investigation.summary}\nNo code changes or PR were made."
                )[:4000],
                artifact_ids=case.investigation.artifact_ids,
            )
        )
        case = await handoff(case)
        return {"case": case.model_copy(update={"phase": "finished"})}

    async def propose(state: BugState) -> BugState:
        case = state["case"]
        try:
            proposal = PlanProposal.model_validate(await services.propose(case))
            case = version_plan(case, proposal)
            plan_message(case, discussion=case.plan.needs_discussion)
        except Exception:  # noqa: BLE001 - report invalid/unavailable planning privately
            return {
                "case": case.model_copy(
                    update={
                        "outcome": ExecutionOutcome(
                            status="blocked",
                            summary="Planning failed or exceeded message/discussion limits. No changes authorized.",
                        ),
                        "approval": None,
                    }
                )
            }
        return {"case": case}

    async def send_plan(state: BugState) -> BugState:
        case = state["case"]
        discussion = case.plan.needs_discussion or bool(
            case.team_event and case.team_event.action == "discuss"
        )
        wait = await send_team(
            case,
            plan_message(case, discussion=discussion),
            "discussion" if discussion else "decision",
        )
        case = await handoff(case)
        return {
            "case": case.model_copy(
                update={"team_wait": wait, "phase": "waiting_team", "approval": None}
            )
        }

    async def execute(state: BugState) -> BugState:
        case = state["case"]
        if case.approval is None or case.approval.revision != case.plan_revision:
            raise ValueError("Missing current approval")
        outcome = ExecutionOutcome(
            status="blocked",
            summary="Plan approved, but the downstream executor is not configured. No fix or PR was executed.",
        )
        if services.execute_approved:
            try:
                outcome = ExecutionOutcome.model_validate(
                    await services.execute_approved(case)
                )
            except Exception:  # noqa: BLE001 - never imply that a failed executor succeeded
                outcome = ExecutionOutcome(
                    status="failed",
                    summary="The approved executor failed. No verified fix or ready PR is claimed; manual inspection is required.",
                )
        return {"case": case.model_copy(update={"outcome": outcome})}

    async def finish(state: BugState) -> BugState:
        case = state["case"]
        text = (
            (
                "Revised plan rejected. Earlier attempts or a draft PR may remain; no further execution is authorized."
                if case.attempt_count
                else "Plan rejected. No changes or PR were made."
            )
            if case.phase == "rejected"
            else case.outcome.summary
        )
        await services.send(
            TeamMessage(
                text=f"Case {case.case_id}\n{text}"[:4000],
                artifact_ids=(
                    [*case.investigation.artifact_ids[:1], case.after_artifact_id]
                    if case.after_artifact_id and case.investigation
                    else []
                ),
            )
        )
        case = await handoff(case)
        return {
            "case": case.model_copy(
                update={"phase": "rejected" if case.phase == "rejected" else "finished"}
            )
        }

    def decision_route(state: BugState) -> str:
        return {
            "approve": "fix_candidate" if services.workflow else "execute_approved",
            "reject": "finish",
            "discuss": "send_team_message",
            "reply": "propose_plan",
        }[state["case"].team_event.action]

    graph = StateGraph(BugState)
    if services.workflow is not None:
        workflow = services.workflow
        if authority is None or arm_ci is None:
            raise ValueError(
                "Integrated execution requires coordinator publication authority"
            )

        async def fix_candidate(state: BugState) -> BugState:
            case = state["case"]
            try:
                case = await workflow.fix(case)
            except Exception:  # noqa: BLE001 - contain private stage failures
                case = terminal(
                    case,
                    "Fix setup failed; manual inspection is required. No ready PR is claimed.",
                )
            return {"case": case}

        async def verify_candidate(state: BugState) -> BugState:
            case = state["case"]
            try:
                case = await workflow.verify(case)
            except Exception:  # noqa: BLE001 - contain private stage failures
                case = terminal(
                    case,
                    "Verification infrastructure failed. No verified or ready PR is claimed.",
                )
            return {"case": case}

        async def publish_candidate(state: BugState) -> BugState:
            case = state["case"]
            try:
                updated = await workflow.publish(case, await authority(case))
            except Exception:  # noqa: BLE001 - transport errors may contain credentials
                updated = terminal(
                    case,
                    "Publication failed; inspect remote state manually. No ready PR is claimed.",
                )
            if updated.workflow_route == "wait_ci":
                await arm_ci(updated.publication)
                await services.send(
                    TeamMessage(
                        text=f"Case {case.case_id}: local verification passed. Draft PR: {updated.publication.pr_url}\nWaiting for exact-head CI; this is not ready or deployed."
                    )
                )
            elif (
                updated.workflow_route == "wait_publication"
                and case.workflow_route != "wait_publication"
            ):
                await services.send(
                    TeamMessage(
                        text=f"Case {case.case_id}: publication transport is uncertain. Reconciling without another fix attempt; no ready PR is claimed."
                    )
                )
            return {"case": updated}

        def wait_ci(state: BugState) -> BugState:
            case = state["case"]
            wait = CIWait(publication=case.publication)
            observation = CIObservation.model_validate(
                interrupt(wait.model_dump(mode="json"))
            )
            return {"case": workflow.observe(case, observation)}

        def wait_publication(state: BugState) -> BugState:
            case = state["case"]
            wait = PublicationWait(
                run_id=case.run_id,
                case_id=case.case_id,
                attempt=case.attempt_count,
                revision=case.plan_revision,
            )
            if PublicationWait.model_validate(interrupt(wait.model_dump())) != wait:
                raise ValueError("Stale publication reconciliation")
            return {"case": case}

        async def mark_ready(state: BugState) -> BugState:
            case = state["case"]
            try:
                case = await workflow.mark_ready(case, await authority(case))
            except Exception:  # noqa: BLE001 - transport errors may contain credentials
                case = terminal(
                    case,
                    "Readiness could not be verified. Inspect PR state manually; no success is claimed.",
                )
            if case.workflow_route == "wait_ci":
                await arm_ci(case.publication)
            return {"case": case}

        async def retry(state: BugState) -> BugState:
            case = await workflow.retry(state["case"])
            if case.workflow_route != "finish":
                await services.send(
                    TeamMessage(
                        text=f"Case {case.case_id}: attempt {case.attempt_count}/3 did not complete. {case.retry_reason[:2000]}\n"
                        + (
                            "A changed approach needs a new plan and explicit approval."
                            if case.workflow_route == "reapprove"
                            else "Retrying within the existing approved scope and global attempt budget."
                        )
                    )
                )
            return {"case": case}

        destinations = {
            "fix": "fix_candidate",
            "verify": "verify_candidate",
            "retry": "retry_or_handoff",
            "publish": "publish_candidate",
            "wait_ci": "wait_for_ci",
            "wait_publication": "wait_for_publication",
            "reapprove": "propose_plan",
            "mark_ready": "mark_ready",
            "finish": "finish",
        }
        for name, node in (
            ("fix_candidate", fix_candidate),
            ("verify_candidate", verify_candidate),
            ("retry_or_handoff", retry),
            ("publish_candidate", publish_candidate),
            ("wait_for_ci", wait_ci),
            ("mark_ready", mark_ready),
        ):
            graph.add_node(name, node)
            graph.add_conditional_edges(
                name, lambda state: state["case"].workflow_route, destinations
            )
        graph.add_node("wait_for_publication", wait_publication)
        graph.add_edge("wait_for_publication", "publish_candidate")
    for name, node in (
        ("investigate", investigate),
        ("request_clarification", clarify),
        ("wait_for_client", wait_for_client),
        ("report_diagnosis", report_diagnosis),
        ("propose_plan", propose),
        ("send_team_message", send_plan),
        ("wait_for_team", wait_for_team),
        ("execute_approved", execute),
        ("finish", finish),
    ):
        graph.add_node(name, node)
    graph.add_edge(START, "investigate")
    graph.add_conditional_edges(
        "investigate",
        diagnosis_route,
        ["propose_plan", "request_clarification", "report_diagnosis"],
    )
    graph.add_edge("request_clarification", "wait_for_client")
    graph.add_edge("wait_for_client", "investigate")
    graph.add_edge("report_diagnosis", END)
    graph.add_conditional_edges(
        "propose_plan",
        lambda state: "finish" if state["case"].outcome else "send_team_message",
        ["finish", "send_team_message"],
    )
    graph.add_edge("send_team_message", "wait_for_team")
    graph.add_conditional_edges(
        "wait_for_team",
        decision_route,
        [
            "fix_candidate" if services.workflow else "execute_approved",
            "finish",
            "send_team_message",
            "propose_plan",
        ],
    )
    graph.add_edge("execute_approved", "finish")
    graph.add_edge("finish", END)
    return graph.compile(checkpointer=InMemorySaver())
