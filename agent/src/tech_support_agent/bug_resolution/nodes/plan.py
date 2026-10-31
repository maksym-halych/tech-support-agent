"""Version plans in trusted code and preserve the human product decision gate."""

from tech_support_agent.bug_resolution.contracts import (
    CaseSnapshot,
    PlanProposal,
    TeamMessage,
)

BOTH_ALTERNATIVES = (
    "Choose the intended behavior by replying to this message:\n"
    "1. One combined Math and Reading plan, keeping the shorter flow.\n"
    "2. A separate goal and plan for each subject, adding steps.\n"
    "A text reply is discussion only. A new plan will require button approval."
)


def version_plan(case: CaseSnapshot, proposal: PlanProposal) -> CaseSnapshot:
    """Invalidate earlier approval and increment revision, never attempt counters."""

    discussion = list(case.discussion)
    if case.team_event and case.team_event.action == "reply":
        discussion.append(case.team_event.text)
    if len(discussion) > 20:
        raise ValueError("Discussion limit reached; manual handoff required")
    if case.scenario == "both" and (not discussion or proposal.product_choice is None):
        proposal = proposal.model_copy(
            update={"needs_discussion": True, "product_choice": None}
        )
    return case.model_copy(
        update={
            "plan": proposal,
            "plan_revision": case.plan_revision + 1,
            "approval": None,
            "team_wait": None,
            "team_event": None,
            "discussion": discussion,
            "phase": "planning",
        }
    )


def plan_message(case: CaseSnapshot, *, discussion: bool) -> TeamMessage:
    """Render plain text without allowing model text to create Telegram markup."""

    if case.plan is None or case.investigation is None:
        raise ValueError("Missing investigation or plan")
    result = case.investigation
    evidence = (
        f"Case {case.case_id} | plan revision {case.plan_revision}\n{result.summary}\n"
    )
    steps = "\n".join(f"- {step}" for step in result.steps)
    plan = case.plan
    details = (
        f"\nExpected behavior: {plan.expected_behavior}\n"
        + "Allowed changes:\n"
        + "\n".join(f"- {item}" for item in plan.allowed_changes)
        + "\n- One new behavior-level test: tests/regression.spec.ts"
        + "\nAcceptance checks:\n"
        + "\n".join(f"- {item}" for item in plan.acceptance_checks)
    )
    suffix = (
        f"\n{case.attempt_count}/3 fix attempts already used. Earlier attempts or a draft PR may remain. Use a button to approve this new revision."
        if case.attempt_count
        else "\nCode has not been changed. Use a button to approve this exact revision."
    )
    if discussion:
        suffix = "\n" + (
            BOTH_ALTERNATIVES
            if case.scenario == "both"
            else "Reply to this message with the intended behavior. Discussion is not permission to edit."
        )
    # Never truncate an approval plan: the human must see every authorized change.
    return TeamMessage(
        text=evidence + steps + details + suffix, artifact_ids=result.artifact_ids
    )
