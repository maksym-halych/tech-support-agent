from pathlib import Path

import pytest

from tech_support_agent.bug_resolution.adapters.commands import (
    CommandError,
    run_command,
)
from tech_support_agent.bug_resolution.adapters.fix_sandbox import (
    freeze_files,
    write_file,
)
from tech_support_agent.bug_resolution.adapters.sandbox import (
    DockerSandbox,
    capture_profile,
)
from tech_support_agent.bug_resolution.evidence import EvidenceStore
from tech_support_agent.bug_resolution.fix_contracts import (
    ApprovedFixPlan,
    CandidateRef,
    FileChange,
    FixPolicyError,
    FixResult,
    FrozenCandidate,
    ReviewResult,
    files_digest,
)
from tech_support_agent.bug_resolution.fixing import FixSession, docker_editor_factory
from tech_support_agent.bug_resolution.nodes.fix import FixNode, FixRequest
from tech_support_agent.bug_resolution.sandbox.protocol import (
    Click,
    Empty,
    Select,
    SourceFile,
)
from tech_support_agent.bug_resolution.verification import (
    BrowserResult,
    CandidateVerifier,
    DockerVerificationBackend,
)
from tech_support_agent.config import DemoSettings
from tests.bug_resolution.integration.test_demo_sandbox import (
    docker_image as docker_image,  # noqa: PLC0414 -- pytest fixture re-export
)
from tests.bug_resolution.integration.test_demo_sandbox import fixture_repository, ref

pytestmark = pytest.mark.docker

REGRESSION = """import { test, expect } from "@playwright/test";
test("approved behavior survives the quiz", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Both Math" }).click();
  await page.getByRole("button", { name: "Continue", exact: true }).click();
  await page.getByLabel("Grade", { exact: true }).selectOption("3");
  await expect(page.getByRole("button", { name: "Continue", exact: true })).toBeEnabled();
  await page.getByRole("button", { name: "Continue", exact: true }).click();
  await expect(page.getByRole("heading", { name: "What would you like to work on?" })).toBeVisible();
  await page.getByLabel("Learning goal", { exact: true }).selectOption("Build confidence");
  await page.getByRole("button", { name: "See demo summary" }).click();
  await expect(page.getByText("Math + Reading", { exact: true })).toBeVisible();
});
"""


@pytest.mark.parametrize("scenario", ["continue", "both"])
async def test_real_docker_fix_red_green_browser_and_fresh_review(
    docker_image: str, tmp_path: Path, scenario: str
) -> None:
    repo = tmp_path / "repo"
    await fixture_repository(repo)
    profile = await capture_profile(
        DemoSettings(
            _env_file=None,
            demo_repo="isolated-fixture/quiz",
            demo_repo_dir=repo,
            sandbox_image=docker_image,
        ),
        scenario,
    )
    path = "src/grade.ts" if scenario == "continue" else "src/subjects.ts"
    approved = ApprovedFixPlan(
        run_id="run",
        case_id=f"case-{scenario}",
        revision=1,
        base_sha=profile.base_sha,
        expected_behavior="Continue reaches goal and the combined summary retains Math and Reading",
        allowed_changes=(path,),
        acceptance_checks=(
            "Grade selection permits Continue",
            "Goal transition works",
            "Summary retains both subjects",
        ),
        approved_by=123,
    )
    subject = FixSession(
        approved.run_id,
        approved.case_id,
        approved.base_sha,
        docker_editor_factory(profile),
    )
    await subject.install_approval(approved)
    leases = []

    async def fixer(plan, tools):
        leases.append(tools)
        old = await tools.read_file(path)
        fixed = (
            old.content.replace(
                "Number.isInteger(grade)", "Number.isInteger(Number(grade))"
            )
            if scenario == "continue"
            else old.content.replace('["Math"]', '["Math", "Reading"]')
        )
        assert fixed != old.content
        await tools.apply_patch(FileChange(path=path, content=fixed))
        await tools.apply_patch(
            FileChange(path="tests/regression.spec.ts", content=REGRESSION)
        )
        return FixResult(
            status="candidate",
            summary="Scripted test fixture applied actual source and behavior test edits",
        )

    async def browser(plan, probe):
        page = await probe.act(Empty(op="inspect"))
        page = await probe.act(Click(ref=ref(page, "Both")))
        page = await probe.act(Click(ref=ref(page, "Continue")))
        page = await probe.act(Select(ref=ref(page, "Grade", "combobox"), value="3"))
        assert not next(e for e in page.elements if e.name == "Continue").disabled
        page = await probe.act(Click(ref=ref(page, "Continue")))
        assert "What would you like" in page.text
        page = await probe.act(
            Select(ref=ref(page, "Learning goal", "combobox"), value="Build confidence")
        )
        page = await probe.act(Click(ref=ref(page, "See demo summary")))
        assert (
            "Math" in page.text
            and "Reading" in page.text
            and "Demo complete" in page.text
        )
        return BrowserResult(
            status="passed",
            checked_acceptance=plan.acceptance_checks,
            observation=page.text,
        )

    async def reviewer(context):
        assert (
            context.plan == approved and context.diff and len(context.check_logs) == 4
        )
        assert context.checks[0].assertion is not None
        assert "messages" not in type(context).model_fields
        return ReviewResult(
            candidate=context.candidate.ref,
            blocking_findings=(),
            scope_matches_plan=True,
            regression_is_behavioral=True,
            regression_not_weakened=True,
            summary="Independent scripted reviewer inspected actual fixture diff and check results",
        )

    try:
        result = await FixNode(subject, fixer)(
            FixRequest(operation_id="first", plan_revision=1)
        )
        assert result.status == "candidate", result
        candidate = result.candidate
        with pytest.raises(FixPolicyError):
            await leases[0].apply_patch(FileChange(path=path, content="late write"))
        store = EvidenceStore(tmp_path)
        backend = DockerVerificationBackend(profile, store, browser)
        verified = await CandidateVerifier(backend, store, reviewer).verify(
            subject, candidate
        )
        assert verified.status == "passed", verified
        assert verified.bundle.regression_on_base.exit_code == 1
        assert verified.bundle.tests_on_candidate.exit_code == 0
        assert verified.bundle.browser.screenshot.size > 1000
        assert subject.attempts_used == 1
    finally:
        await subject.close()


async def test_freezer_stops_background_writers_and_removes_container(
    docker_image: str, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    await fixture_repository(repo)
    profile = await capture_profile(
        DemoSettings(
            _env_file=None,
            demo_repo="fixture/quiz",
            demo_repo_dir=repo,
            sandbox_image=docker_image,
        ),
        "both",
    )
    sandbox = await DockerSandbox.prepare(profile)
    try:
        await run_command(
            "docker",
            "exec",
            "--detach",
            sandbox.name,
            "/usr/local/bin/node",
            "-e",
            "const fs=require('fs');let n=0;const write=()=>fs.writeFileSync('/work/project/src/heartbeat.ts','export const heartbeat = '+n+++';');write();setInterval(write,1)",
        )
        snapshot = await freeze_files(sandbox)
        assert any(file.path == "src/heartbeat.ts" for file in snapshot)
        assert sandbox.closed
        assert not (
            await run_command(
                "docker", "ps", "--all", "--quiet", "--filter", f"name=^{sandbox.name}$"
            )
        ).strip()
    finally:
        await sandbox.close()


async def test_real_write_rejects_symlink_and_destroys_writer(
    docker_image: str, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    await fixture_repository(repo)
    profile = await capture_profile(
        DemoSettings(
            _env_file=None,
            demo_repo="fixture/quiz",
            demo_repo_dir=repo,
            sandbox_image=docker_image,
        ),
        "both",
    )
    sandbox = await DockerSandbox.prepare(profile)
    try:
        await run_command(
            "docker",
            "exec",
            sandbox.name,
            "/usr/local/bin/node",
            "-e",
            "require('fs').symlinkSync('/tmp/escape','/work/project/src/escape.ts')",
        )
        with pytest.raises(CommandError):
            await write_file(
                sandbox, FileChange(path="src/escape.ts", content="escaped")
            )
        assert sandbox.closed
    finally:
        await sandbox.close()


@pytest.mark.parametrize(
    "test_source",
    [
        'import {test} from "@playwright/test"; import "../src/nonexistent-helper"; test("missing helper", () => {});',
        'import {test,expect} from "@playwright/test"; test.skip("disabled", () => { expect(true).toBe(false); });',
        'import {test,expect} from "@playwright/test"; test("weak", () => { expect(true).toBe(true); });',
    ],
)
async def test_real_invalid_regression_never_counts_as_red(
    docker_image: str, tmp_path: Path, test_source: str
) -> None:
    repo = tmp_path / "repo"
    await fixture_repository(repo)
    profile = await capture_profile(
        DemoSettings(
            _env_file=None,
            demo_repo="fixture/quiz",
            demo_repo_dir=repo,
            sandbox_image=docker_image,
        ),
        "both",
    )
    sandbox = await DockerSandbox.prepare(profile)
    base = sandbox.initial_files
    await sandbox.close()
    files = (*base, SourceFile(path="tests/regression.spec.ts", content=test_source))
    candidate = FrozenCandidate(
        ref=CandidateRef(
            run_id="run",
            case_id="case",
            attempt=1,
            plan_revision=1,
            base_sha=profile.base_sha,
            digest=files_digest(files),
        ),
        files=files,
        base_files=base,
    )

    async def no_browser(plan, probe):
        pytest.fail("Red failure must not invoke browser verification")

    backend = DockerVerificationBackend(profile, EvidenceStore(tmp_path), no_browser)
    result = await backend.check(candidate, "regression_on_base")
    assert result.status != "passed" and result.assertion is None
