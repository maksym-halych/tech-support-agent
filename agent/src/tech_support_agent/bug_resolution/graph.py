"""A private graph: investigate, optionally clarify once, deliver, finish."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.pregel import Pregel

from tech_support_agent.bug_resolution.contracts import (
    CaseSnapshot,
    Clarification,
    DeliveryReceipt,
    HandedOff,
    IntakeResult,
)
from tech_support_agent.bug_resolution.nodes.intake import BugState, wait_for_client

type PublishEvent = Callable[[str, Clarification | HandedOff], Awaitable[None]]


@dataclass(frozen=True)
class IntakeServices:
    """Private dependencies; tests inject doubles, runtime never simulates success."""

    investigate: Callable[[CaseSnapshot], Awaitable[IntakeResult]]
    deliver: Callable[[CaseSnapshot], Awaitable[DeliveryReceipt]]


async def unavailable_investigator(case: CaseSnapshot) -> IntakeResult:
    """Fail closed until a real investigator is wired in a later phase."""

    raise RuntimeError("Investigator is not configured")


async def unavailable_delivery(case: CaseSnapshot) -> DeliveryReceipt:
    """Fail closed until a real team delivery service is wired."""

    raise RuntimeError("Team delivery is not configured")


def build_bug_graph(services: IntakeServices, publish: PublishEvent) -> Pregel:
    """Compile the private graph with its own in-memory checkpoints.

    Args:
        services: Investigator and team delivery dependencies.
        publish: Coordinator-owned safe event sink.
    Returns:
        A graph that must never be registered on Agent Server.
    """

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
