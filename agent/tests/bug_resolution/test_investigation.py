import base64
from io import BytesIO
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage
from PIL import Image
from pydantic import ValidationError

from tech_support_agent.bug_resolution.adapters.sandbox import ImageArtifact, export_png
from tech_support_agent.bug_resolution.agents import StageLimits
from tech_support_agent.bug_resolution.contracts import (
    CaseSnapshot,
    InvestigationResult,
    PlanProposal,
)
from tech_support_agent.bug_resolution.nodes.investigate import investigate_case
from tech_support_agent.bug_resolution.nodes.plan import (
    BOTH_ALTERNATIVES,
    plan_message,
    version_plan,
)
from tech_support_agent.bug_resolution.sandbox.protocol import (
    Operation,
    PageSnapshot,
    Screenshot,
)
from tests.bug_resolution.conftest import report
from tests.conftest import scripted_model, tool_call_message

ARTIFACT = "a" * 32


def case(scenario: str = "continue") -> CaseSnapshot:
    return CaseSnapshot(
        run_id="run", case_id="case", request=report(), scenario=scenario
    )


def confirmed() -> InvestigationResult:
    return InvestigationResult(
        status="confirmed",
        summary="Actual selected grade blocks navigation",
        steps=["Select grade 3", "Observe disabled Continue"],
        artifact_ids=[ARTIFACT],
    )


def proposal(**updates: object) -> PlanProposal:
    return PlanProposal(
        **{
            "expected_behavior": "Selected grade enables Continue and reaches the next step",
            "allowed_changes": ["src/main.tsx"],
            "acceptance_checks": ["Select grade, click Continue, observe goal step"],
            **updates,
        }
    )


def result_message(result: InvestigationResult) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": "submit_result",
                "args": result.model_dump(),
                "id": "done",
            }
        ],
    )


class FakeSandbox:
    def __init__(self, *, broken: bool = False) -> None:
        self.operations = []
        self.broken = broken

    async def request(self, operation: Operation) -> PageSnapshot:
        self.operations.append(operation.op)
        if self.broken:
            raise RuntimeError("private infrastructure failure")
        return PageSnapshot(path="/", text="Continue disabled", elements=[])

    async def screenshot(self, directory: Path) -> ImageArtifact:
        self.operations.append("screenshot")
        data = BytesIO()
        Image.new("RGB", (2, 2)).save(data, format="PNG")
        return export_png(
            Screenshot(
                artifact_id=ARTIFACT,
                mime="image/png",
                data=base64.b64encode(data.getvalue()).decode(),
            ),
            directory,
        )


async def test_actual_tool_artifact_is_required_for_confirmation(
    tmp_path: Path,
) -> None:
    sandbox = FakeSandbox()
    artifacts = {}
    model = scripted_model(
        tool_call_message("inspect_page", 1),
        tool_call_message("screenshot", 1),
        result_message(confirmed()),
    )
    result = await investigate_case(
        case(), sandbox, model, tmp_path, StageLimits(), artifacts
    )
    assert result == confirmed() and artifacts[ARTIFACT].path.is_file()
    assert sandbox.operations == ["navigate", "inspect", "screenshot"]
    with pytest.raises(ValueError, match="artifact"):
        await investigate_case(
            case(),
            sandbox,
            scripted_model(result_message(confirmed())),
            tmp_path,
            StageLimits(),
            artifacts,
        )


async def test_image_without_browser_observation_is_not_confirmation(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="browser"):
        await investigate_case(
            case(),
            FakeSandbox(),
            scripted_model(
                tool_call_message("screenshot", 1), result_message(confirmed())
            ),
            tmp_path,
            StageLimits(),
            {},
        )


@pytest.mark.parametrize("status", ["unconfirmed", "blocked", "needs_details"])
async def test_unconfirmed_and_blocked_are_distinct_typed_results(
    tmp_path: Path, status: str
) -> None:
    result = InvestigationResult(
        status=status, summary="Actual outcome", question="Which grade?"
    )
    actual = await investigate_case(
        case(),
        FakeSandbox(),
        scripted_model(result_message(result)),
        tmp_path,
        StageLimits(),
        {},
    )
    assert actual.status == status


def test_model_cannot_set_approval_or_attempts() -> None:
    for extra in (
        {"approved_by": 1},
        {"revision": 2},
        {"ready": True},
        {"attempt_count": 0},
    ):
        with pytest.raises(ValidationError):
            PlanProposal.model_validate({**proposal().model_dump(), **extra})
    with pytest.raises(ValidationError):
        InvestigationResult(status="confirmed", summary="No evidence")


@pytest.mark.parametrize("choice", ["combined", "separate", None])
def test_both_requires_discussion_even_when_model_preselects(
    choice: str | None,
) -> None:
    initial = case("both").model_copy(update={"investigation": confirmed()})
    current = version_plan(initial, proposal(product_choice=choice))
    assert current.plan.needs_discussion and current.plan.product_choice is None
    message = plan_message(current, discussion=True)
    assert BOTH_ALTERNATIVES in message.text
    assert current.plan_revision == 1 and current.approval is None


@pytest.mark.parametrize(
    "path",
    [
        "fix the application",
        "src/*",
        "../src/main.tsx",
        "tests/smoke.spec.ts",
        ".github/workflows/verify.yml",
    ],
)
def test_approval_scope_rejects_prose_globs_and_protected_paths(path: str) -> None:
    with pytest.raises(ValidationError):
        proposal(allowed_changes=[path])
