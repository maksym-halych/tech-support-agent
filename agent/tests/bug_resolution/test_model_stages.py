"""Model-backed stage capability and budget tests, with no external execution."""

from pathlib import Path

import pytest
from langchain_core.messages import AIMessage
from pydantic import ValidationError

from tech_support_agent.bug_resolution.agents import (
    BudgetExceeded,
    StageFailure,
    StageLimits,
)
from tech_support_agent.bug_resolution.evidence import EvidenceStore
from tech_support_agent.bug_resolution.model_stages import ModelStages
from tech_support_agent.bug_resolution.sandbox.protocol import PageSnapshot
from tech_support_agent.bug_resolution.verification import (
    BrowserProbe,
    ReviewInput,
    candidate_diff,
)
from tech_support_agent.config import PublicationSettings
from tests.bug_resolution.fix_fixtures import edit_and_freeze, plan, session
from tests.bug_resolution.test_verification import bundle_for
from tests.conftest import scripted_model


def call(name: str, **args: object) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": name}])


async def test_fixer_model_can_only_edit_approved_source_and_new_test() -> None:
    ledger, editors = session()
    await ledger.install_approval(plan())
    tools = await ledger.begin_attempt("one", 1)
    stages = ModelStages(
        scripted_model(
            call("read_file", path="src/grade.ts"),
            call("apply_patch", path="src/grade.ts", content="fixed"),
            call(
                "apply_patch", path="tests/regression.spec.ts", content="UI expectation"
            ),
            call("submit_result", status="candidate", summary="Scoped candidate"),
        ),
        StageLimits(),
        StageLimits(),
    )
    result = await stages.fix(plan(), tools, "Actual previous failure")
    assert result.status == "candidate" and editors[0].writes == 2
    assert (await ledger.freeze(tools)).ref.attempt == 1
    await ledger.close()


async def test_fixer_budget_prevents_writes() -> None:
    ledger, editors = session()
    await ledger.install_approval(plan())
    tools = await ledger.begin_attempt("one", 1)
    stages = ModelStages(
        scripted_model(call("apply_patch", path="src/grade.ts", content="bad")),
        StageLimits(tool_calls=0),
        StageLimits(),
    )
    with pytest.raises(BudgetExceeded):
        await stages.fix(plan(), tools)
    assert editors[0].writes == 0
    await ledger.close()


async def test_reviewer_cannot_write_or_return_approval_fields(tmp_path: Path) -> None:
    ledger, _ = session()
    await ledger.install_approval(plan())
    candidate = await edit_and_freeze(ledger)
    bundle = bundle_for(candidate, EvidenceStore(tmp_path))
    context = ReviewInput(
        plan=plan(),
        candidate=candidate,
        diff=candidate_diff(candidate),
        checks=(bundle.build,),
        check_logs=("actual fixture build log",),
        browser=bundle.browser,
    )
    for message in (
        call("apply_patch", path="src/grade.ts", content="bad"),
        call(
            "submit_result",
            blocking_findings=[],
            scope_matches_plan=True,
            regression_is_behavioral=True,
            regression_not_weakened=True,
            summary="claim",
            approved_by=123,
        ),
    ):
        stages = ModelStages(scripted_model(message), StageLimits(), StageLimits())
        with pytest.raises(StageFailure):
            await stages.review(context)
    stages = ModelStages(
        scripted_model(
            call("read_diff"),
            call("read_file", path="tests/regression.spec.ts"),
            call("check_log", kind="build"),
            call(
                "submit_result",
                blocking_findings=[],
                scope_matches_plan=True,
                regression_is_behavioral=True,
                regression_not_weakened=True,
                summary="Fixture review",
            ),
        ),
        StageLimits(),
        StageLimits(),
    )
    assert (await stages.review(context)).candidate == candidate.ref
    await ledger.close()


async def test_browser_model_uses_observations_but_has_no_file_or_check_tools() -> None:
    class Browser:
        async def request(self, command):
            return PageSnapshot(path="/", text="Fixture page", elements=[])

    for name in ("apply_patch", "read_file", "run_check", "publish"):
        stages = ModelStages(scripted_model(call(name)), StageLimits(), StageLimits())
        with pytest.raises(StageFailure):
            await stages.browser(plan(), BrowserProbe(Browser()))
    output = call(
        "submit_result",
        status="passed",
        checked_acceptance=list(plan().acceptance_checks),
        observation="Fixture observation",
    )
    stages = ModelStages(scripted_model(output), StageLimits(), StageLimits())
    with pytest.raises(ValueError, match="actual observations"):
        await stages.browser(plan(), BrowserProbe(Browser()))
    stages = ModelStages(
        scripted_model(call("inspect_page"), output), StageLimits(), StageLimits()
    )
    result = await stages.browser(plan(), BrowserProbe(Browser()))
    assert result.checked_acceptance == plan().acceptance_checks


def test_live_publication_requires_separate_explicit_opt_in() -> None:
    assert not PublicationSettings(_env_file=None).publication_authorized
    with pytest.raises(ValidationError):
        PublicationSettings(_env_file=None, publication_authorized=True)
    configured = PublicationSettings(
        _env_file=None, github_token="offline-test", github_workflow_id=17
    )
    assert not configured.publication_authorized
