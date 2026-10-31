import asyncio

import pytest
from pydantic import ValidationError

from tech_support_agent.bug_resolution.fix_contracts import (
    FileChange,
    FixPolicyError,
    FixResult,
)
from tech_support_agent.bug_resolution.nodes.fix import FixNode, FixRequest
from tech_support_agent.bug_resolution.sandbox.protocol import (
    Prepare,
    RunCheck,
    SourceFile,
)
from tests.bug_resolution.fix_fixtures import edit_and_freeze, plan, session


async def test_no_write_before_current_approval_and_no_duplicate_attempt() -> None:
    subject, editors = session()
    with pytest.raises(FixPolicyError):
        await subject.begin_attempt("one", 1)
    assert not editors and subject.attempts_used == 0
    await subject.install_approval(plan())
    with pytest.raises(FixPolicyError):
        await subject.begin_attempt("one", 2)
    first, duplicate = await asyncio.gather(
        subject.begin_attempt("one", 1), subject.begin_attempt("one", 1)
    )
    assert first is duplicate and len(editors) == subject.attempts_used == 1
    with pytest.raises(FixPolicyError):
        await subject.begin_attempt("another", 1)
    await subject.close()


@pytest.mark.parametrize(
    "path",
    [
        "package.json",
        "../src/grade.ts",
        "/src/grade.ts",
        "src/../grade.ts",
        ".git/config",
        "tests/smoke.spec.ts",
        "tests/other.spec.ts",
        "src/node_modules/evil.ts",
        "src/.hidden.ts",
    ],
)
def test_protected_paths(path: str) -> None:
    with pytest.raises(ValidationError):
        FileChange(path=path, content="bad")


@pytest.mark.parametrize(
    "changes", [{"case_id": "other"}, {"run_id": "old"}, {"base_sha": "b" * 40}]
)
async def test_wrong_approval_identity(changes: dict) -> None:
    subject, _ = session()
    with pytest.raises(FixPolicyError):
        await subject.install_approval(plan(**changes))


async def test_scope_probe_and_revoked_writer() -> None:
    subject, editors = session()
    await subject.install_approval(plan())
    tools = await subject.begin_attempt("one", 1)
    with pytest.raises(FixPolicyError):
        await tools.apply_patch(FileChange(path="src/unapproved.ts", content="bad"))
    with pytest.raises(FixPolicyError):
        await tools.inspect(Prepare(files=list(editors[0].initial_files)))
    assert (await tools.run_check(RunCheck(kind="tests"))).status == "passed"
    assert subject.verification is None
    await tools.apply_patch(FileChange(path="src/grade.ts", content="fixed"))
    await tools.apply_patch(
        FileChange(path="tests/regression.spec.ts", content="new test")
    )
    candidate = await subject.freeze(tools)
    assert editors[0].closed
    with pytest.raises(FixPolicyError):
        await tools.apply_patch(FileChange(path="src/grade.ts", content="late"))
    with pytest.raises(ValidationError):
        candidate.files[0].content = "late"
    with pytest.raises(FixPolicyError):
        await subject.begin_attempt("one", 1)


async def test_freeze_detects_out_of_band_protected_change() -> None:
    subject, editors = session()
    await subject.install_approval(plan())
    tools = await subject.begin_attempt("one", 1)
    await tools.apply_patch(FileChange(path="src/grade.ts", content="fixed"))
    await tools.apply_patch(
        FileChange(path="tests/regression.spec.ts", content="new test")
    )
    editors[0].files["tests/smoke.spec.ts"] = SourceFile(
        path="tests/smoke.spec.ts", content="disabled"
    )
    with pytest.raises(FixPolicyError):
        await subject.freeze(tools)
    assert subject.candidate is None and editors[0].closed
    with pytest.raises(FixPolicyError):
        await tools.read_file("src/grade.ts")


async def test_tool_and_wall_clock_budgets() -> None:
    clock = [0.0]
    subject, _ = session(clock=lambda: clock[0], max_tools=1)
    await subject.install_approval(plan())
    tools = await subject.begin_attempt("one", 1)
    await tools.read_file("src/grade.ts")
    with pytest.raises(FixPolicyError, match="budget"):
        await tools.read_file("src/grade.ts")
    await subject.retry_or_handoff("budget")
    tools = await subject.begin_attempt("two", 1)
    clock[0] = 301
    with pytest.raises(FixPolicyError, match="budget"):
        await tools.apply_patch(FileChange(path="src/grade.ts", content="late"))
    await subject.close()


async def test_fix_node_executes_once_and_times_out_honestly() -> None:
    subject, editors = session()
    await subject.install_approval(plan())
    calls = 0

    async def fixer(approved, tools):
        nonlocal calls
        calls += 1
        await tools.apply_patch(FileChange(path="src/grade.ts", content="fixed"))
        await tools.apply_patch(
            FileChange(path="tests/regression.spec.ts", content="new test")
        )
        return FixResult(status="candidate", summary="Actual fixture edit")

    node = FixNode(subject, fixer)
    request = FixRequest(operation_id="one", plan_revision=1)
    a, b = await asyncio.gather(node(request), node(request))
    assert a == b and a.candidate and calls == 1 and editors[0].closed
    await subject.close()
    slow, editors = session(max_seconds=0.01)
    await slow.install_approval(plan())

    async def hanging(approved, tools):
        await asyncio.sleep(10)

    result = await FixNode(slow, hanging)(request)
    assert result.status == "blocked" and result.candidate is None
    assert slow.attempts_used == 1 and editors[0].closed


async def test_new_candidate_invalidates_previous_identity() -> None:
    subject, _ = session()
    await subject.install_approval(plan())
    first = await edit_and_freeze(subject)
    await subject.retry_or_handoff("CI failure")
    second = await edit_and_freeze(subject, "second")
    assert (
        first.ref.digest == second.ref.digest
        and first.ref.attempt != second.ref.attempt
    )
    with pytest.raises(FixPolicyError):
        subject.require_current(first)
