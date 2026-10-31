"""Keep the waiting node free of delivery and other repeatable side effects."""

from typing import TypedDict

from langgraph.types import interrupt

from tech_support_agent.bug_resolution.contracts import (
    CaseSnapshot,
    ClarificationRequest,
    ClientWait,
)


class BugState(TypedDict):
    """LangGraph glue; the payload itself is a validated private snapshot."""

    case: CaseSnapshot


def wait_for_client(state: BugState) -> BugState:
    """Pause for exactly the matching typed clarification answer.

    Raises:
        ValueError: If the coordinator supplies a mismatched resume.
    """

    case = CaseSnapshot.model_validate(state["case"])
    wait = ClientWait(
        case_id=case.case_id, clarification_cursor=case.clarification_cursor
    )
    answer = ClarificationRequest.model_validate(interrupt(wait.model_dump()))
    if (
        answer.source_thread_id != case.request.source_thread_id
        or answer.clarification_cursor != wait.clarification_cursor
    ):
        raise ValueError("Mismatched private resume")
    return {"case": case.model_copy(update={"clarification": answer})}
