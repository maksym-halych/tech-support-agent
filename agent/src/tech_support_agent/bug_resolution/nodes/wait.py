"""Side-effect-free private team interrupts."""

from langgraph.types import interrupt

from tech_support_agent.bug_resolution.contracts import (
    ApprovedPlan,
    CaseSnapshot,
    TeamEvent,
)
from tech_support_agent.bug_resolution.nodes.intake import BugState


def wait_for_team(state: BugState) -> BugState:
    """Resume only a matching typed event; mint approval outside the model context."""

    case = CaseSnapshot.model_validate(state["case"])
    wait = case.team_wait
    if wait is None:
        raise ValueError("Missing team wait")
    event = TeamEvent.model_validate(interrupt(wait.model_dump()))
    if event.wait != wait:
        raise ValueError("Mismatched team resume")
    approval = None
    phase = "planning"
    if event.action == "approve":
        if wait.mode != "decision" or case.plan is None or case.plan.needs_discussion:
            raise ValueError("Discussion is not approval")
        approval = ApprovedPlan(
            revision=case.plan_revision,
            expected_behavior=case.plan.expected_behavior,
            allowed_changes=case.plan.allowed_changes,
            acceptance_checks=case.plan.acceptance_checks,
            approved_by=event.user_id,
        )
        phase = "approved"
    elif event.action == "reject":
        phase = "rejected"
    elif event.action == "reply" and wait.mode != "discussion":
        raise ValueError("Reply outside discussion")
    return {
        "case": case.model_copy(
            update={
                "team_event": event,
                "approval": approval,
                "phase": phase,
            }
        )
    }
