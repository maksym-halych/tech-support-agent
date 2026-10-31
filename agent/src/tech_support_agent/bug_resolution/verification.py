"""Fail-closed verification of one frozen candidate in disposable Docker copies."""

import asyncio
import difflib
import hashlib
import logging
import re
from typing import Literal, Protocol

from pydantic import ValidationError

from tech_support_agent.bug_resolution.adapters.fix_sandbox import (
    freeze_files,
    run_tests,
)
from tech_support_agent.bug_resolution.adapters.sandbox import (
    DemoProfile,
    DockerSandbox,
)
from tech_support_agent.bug_resolution.evidence import EvidenceStore
from tech_support_agent.bug_resolution.fix_contracts import (
    REGRESSION_PATH,
    ApprovedFixPlan,
    BrowserEvidence,
    CheckEvidence,
    FixPolicyError,
    FrozenCandidate,
    ReviewResult,
    TestReport,
    VerificationBundle,
    VerificationResult,
    files_digest,
)
from tech_support_agent.bug_resolution.fixing import FixSession, validate_scope
from tech_support_agent.bug_resolution.sandbox.protocol import (
    CheckResult,
    Click,
    Empty,
    Navigate,
    PageSnapshot,
    RunCheck,
    Screenshot,
    Select,
    WireModel,
)

logger = logging.getLogger(__name__)


class VerificationUnavailable(RuntimeError):
    """Runtime verification is disabled until its trust boundary is repaired."""


def require_trusted_verification_runtime() -> None:
    """Fail closed; neither test stdout nor mutable check inputs are authoritative."""

    raise VerificationUnavailable(
        "Runtime verification blocked: generated tests can forge reporter stdout "
        "or exit early, and same-owner processes can mutate and restore verification "
        "inputs. A trusted report lifecycle and OS-enforced immutable inputs are "
        "not implemented. Prior Docker results must not authorize publication."
    )


class BrowserResult(WireModel):
    status: Literal["passed", "failed", "blocked"]
    checked_acceptance: tuple[str, ...]
    observation: str


class BrowserProbe:
    """Fresh browser capability without file writes, checks, or a raw container handle."""

    def __init__(self, sandbox: DockerSandbox) -> None:
        self._sandbox = sandbox
        self.calls = 0

    async def act(self, command: Empty | Navigate | Click | Select) -> PageSnapshot:
        """Observe the candidate through bounded, origin-restricted browser operations."""

        if not isinstance(command, (Empty, Navigate, Click, Select)) or (
            isinstance(command, Empty) and command.op != "inspect"
        ):
            raise FixPolicyError("Browser verification has no write/check capability")
        if self.calls >= 30:
            raise FixPolicyError("Browser verification tool budget exhausted")
        self.calls += 1
        result = await self._sandbox.request(command)
        if not isinstance(result, PageSnapshot):
            raise FixPolicyError("Expected a real browser observation")
        return result


class BrowserVerifier(Protocol):
    async def __call__(
        self, plan: ApprovedFixPlan, browser: BrowserProbe
    ) -> BrowserResult: ...


class ReviewInput(WireModel):
    """Independent context deliberately excludes fixer messages and private reasoning."""

    plan: ApprovedFixPlan
    candidate: FrozenCandidate
    diff: str
    checks: tuple[CheckEvidence, ...]
    check_logs: tuple[str, ...]
    browser: BrowserEvidence


class Reviewer(Protocol):
    async def __call__(self, context: ReviewInput) -> ReviewResult: ...


class VerificationBackend(Protocol):
    async def check(self, candidate: FrozenCandidate, kind: str) -> CheckEvidence: ...
    async def browser(
        self, candidate: FrozenCandidate, plan: ApprovedFixPlan
    ) -> BrowserEvidence: ...


def regression_hash(candidate: FrozenCandidate) -> str:
    """Bind both executions to the exact same newly created regression source."""

    regression = next(file for file in candidate.files if file.path == REGRESSION_PATH)
    return hashlib.sha256(regression.content.encode()).hexdigest()


def expected_red(report: TestReport) -> bool:
    """Require a failed UI expectation, not collection, setup, or test-level timeout."""

    if report.errors or len(report.tests) != 1:
        return False
    test = report.tests[0]
    if (
        test.file != REGRESSION_PATH
        or test.status != "failed"
        or test.expected_status != "passed"
        or not test.assertions
        or not test.errors
    ):
        return False
    for assertion in test.assertions:
        if (
            assertion.file != REGRESSION_PATH
            or not re.search(
                r"to(BeEnabled|BeDisabled|BeVisible|HaveText|ContainText|HaveValue|HaveAttribute|HaveCount|HaveURL)",
                assertion.title,
            )
            or not assertion.message
        ):
            return False
    # Every error must be explained by a captured expectation; a later teardown
    # error cannot be hidden behind an earlier valid assertion failure.
    return all(
        any(error == assertion.message for assertion in test.assertions)
        for error in test.errors
    )


def all_tests_green(report: TestReport) -> bool:
    """Require the one regression plus all three fixed smoke tests, with no skips."""

    return (
        not report.errors
        and len(report.tests) == 4
        and sum(test.file == REGRESSION_PATH for test in report.tests) == 1
        and sum(test.file == "tests/smoke.spec.ts" for test in report.tests) == 3
        and len({(test.file, test.title) for test in report.tests}) == 4
        and all(
            test.status == test.expected_status == "passed"
            and not test.errors
            and not test.assertions
            for test in report.tests
        )
    )


def validate_gates(
    bundle: VerificationBundle, candidate: FrozenCandidate, plan: ApprovedFixPlan
) -> None:
    """Check every gate and correlation again at the readiness/publication boundary."""

    validate_scope(candidate, plan)
    ref = candidate.ref
    checks = (
        bundle.regression_on_base,
        bundle.tests_on_candidate,
        bundle.typecheck,
        bundle.build,
    )
    if bundle.candidate != ref or any(
        item.candidate != ref for item in (*checks, bundle.browser, bundle.review)
    ):
        raise FixPolicyError("Mixed candidate verification")
    if tuple(item.kind for item in checks) != (
        "regression_on_base",
        "tests",
        "typecheck",
        "build",
    ):
        raise FixPolicyError("Missing or substituted check")
    if any(item.status != "passed" for item in checks):
        raise FixPolicyError("Not every local check passed")
    red, green = checks[:2]
    if (
        red.exit_code != 1
        or red.report is None
        or not expected_red(red.report)
        or red.assertion not in red.report.tests[0].assertions
    ):
        raise FixPolicyError("Base failure is not behavioral red evidence")
    if (
        green.exit_code != 0
        or green.report is None
        or not all_tests_green(green.report)
    ):
        raise FixPolicyError("Candidate test suite is incomplete")
    if any(item.exit_code != 0 for item in checks[2:]):
        raise FixPolicyError("Build/typecheck did not exit successfully")
    if (
        red.regression_sha256 != regression_hash(candidate)
        or green.regression_sha256 != red.regression_sha256
    ):
        raise FixPolicyError("Red and green did not use the identical test")
    browser, review = bundle.browser, bundle.review
    if (
        browser.status != "passed"
        or browser.screenshot is None
        or browser.screenshot.kind != "png"
        or browser.checked_acceptance != plan.acceptance_checks
    ):
        raise FixPolicyError("Independent browser verification is incomplete")
    if review.blocking_findings or not all(
        (
            review.scope_matches_plan,
            review.regression_is_behavioral,
            review.regression_not_weakened,
        )
    ):
        raise FixPolicyError("Independent review has blocking findings")
    for check in checks:
        if check.log.kind != "log" or check.log.candidate != ref:
            raise FixPolicyError("Check log provenance mismatch")
    if browser.screenshot.candidate != ref:
        raise FixPolicyError("Screenshot provenance mismatch")


class DockerVerificationBackend:
    """Disabled runtime backend pending trusted reporting and immutable check inputs."""

    def __init__(
        self,
        profile: DemoProfile,
        evidence: EvidenceStore,
        browser_verifier: BrowserVerifier,
    ) -> None:
        self.profile, self.evidence, self.browser_verifier = (
            profile,
            evidence,
            browser_verifier,
        )

    async def check(self, candidate: FrozenCandidate, kind: str) -> CheckEvidence:
        """Run fixed commands, record actual logs, and classify behavioral red narrowly."""

        require_trusted_verification_runtime()
        if kind not in ("regression_on_base", "tests", "typecheck", "build"):
            raise FixPolicyError("Unknown fixed check")
        if candidate.ref.base_sha != self.profile.base_sha:
            raise FixPolicyError("Verification base mismatch")
        inputs = candidate.files
        if kind == "regression_on_base":
            inputs = candidate.base_files + tuple(
                file for file in candidate.files if file.path == REGRESSION_PATH
            )
        sandbox = await DockerSandbox.prepare(self.profile, files=inputs)
        try:
            report = None
            assertion = None
            if kind in ("regression_on_base", "tests"):
                execution = await run_tests(
                    sandbox, regression=kind == "regression_on_base"
                )
                output, code = execution.output, execution.exit_code
                try:
                    report = TestReport.model_validate_json(output)
                except ValidationError, ValueError:
                    status = "blocked"
                else:
                    if kind == "regression_on_base":
                        valid = (
                            execution.status == "failed"
                            and code == 1
                            and expected_red(report)
                        )
                        assertion = report.tests[0].assertions[0] if valid else None
                    else:
                        valid = (
                            execution.status == "passed"
                            and code == 0
                            and all_tests_green(report)
                        )
                    status = "passed" if valid else "failed"
                if execution.status == "timeout":
                    status = "blocked"
            else:
                result = await sandbox.request(RunCheck(kind=kind))
                assert isinstance(result, CheckResult)
                output, code = result.output, result.exit_code
                status = "blocked" if result.status == "timeout" else result.status
            actual = await freeze_files(sandbox)
            if files_digest(actual) != files_digest(inputs):
                status = "blocked"
                output += "\nCheck modified its source/test/harness inputs."
            log = self.evidence.record_log(candidate.ref, output)
            return CheckEvidence(
                candidate=candidate.ref,
                kind=kind,
                status=status,
                exit_code=code,
                log=log,
                report=report,
                assertion=assertion,
                regression_sha256=regression_hash(candidate)
                if kind in ("regression_on_base", "tests")
                else None,
            )
        finally:
            await sandbox.close()

    async def browser(
        self, candidate: FrozenCandidate, plan: ApprovedFixPlan
    ) -> BrowserEvidence:
        """Repeat the approved behavior in a new container/context and capture a real PNG."""

        require_trusted_verification_runtime()
        sandbox = await DockerSandbox.prepare(self.profile, files=candidate.files)
        try:
            probe = BrowserProbe(sandbox)
            async with asyncio.timeout(180):
                result = BrowserResult.model_validate(
                    await self.browser_verifier(plan, probe)
                )
            if probe.calls == 0:
                raise FixPolicyError("Browser verifier produced no actual observations")
            png = await sandbox.request(Empty(op="screenshot"))
            assert isinstance(png, Screenshot)
            artifact = self.evidence.record_png(candidate.ref, png)
            actual = await freeze_files(sandbox)
            if files_digest(actual) != candidate.ref.digest:
                raise FixPolicyError("Browser modified candidate inputs")
            return BrowserEvidence(
                candidate=candidate.ref,
                status=result.status,
                checked_acceptance=result.checked_acceptance,
                observation=result.observation,
                screenshot=artifact,
            )
        finally:
            await sandbox.close()


def candidate_diff(candidate: FrozenCandidate) -> str:
    """Render an immutable text diff for a fresh reviewer context, not a shell command."""

    base = {file.path: file.content for file in candidate.base_files}
    return "".join(
        "".join(
            difflib.unified_diff(
                base.get(file.path, "").splitlines(keepends=True),
                file.content.splitlines(keepends=True),
                fromfile=f"a/{file.path}",
                tofile=f"b/{file.path}",
            )
        )
        for file in candidate.files
        if base.get(file.path) != file.content
    )


class CandidateVerifier:
    """Trusted orchestrator; absent integrations or partial success never mean passed."""

    def __init__(
        self, backend: VerificationBackend, evidence: EvidenceStore, reviewer: Reviewer
    ) -> None:
        self.backend, self.evidence, self.reviewer = backend, evidence, reviewer

    async def verify(
        self, session: FixSession, candidate: FrozenCandidate
    ) -> VerificationResult:
        """Run all gates against a current candidate and store only a complete bundle."""

        checks: list[CheckEvidence] = []
        browser: BrowserEvidence | None = None
        review: ReviewResult | None = None
        try:
            plan = session.require_current(candidate)
            for kind in ("regression_on_base", "tests", "typecheck", "build"):
                session.require_current(candidate)
                check = await self.backend.check(candidate, kind)
                checks.append(check)
                if check.status != "passed":
                    return VerificationResult(
                        status=check.status,
                        reason=f"{kind}: {check.status}",
                        checks=tuple(checks),
                    )
            browser = await self.backend.browser(candidate, plan)
            logs = []
            for check in checks:
                self.evidence.validate(check.log, candidate.ref)
                logs.append(
                    (
                        self.evidence.directory / f"{check.log.artifact_id}.log"
                    ).read_text()
                )
            context = ReviewInput(
                plan=plan,
                candidate=candidate,
                diff=candidate_diff(candidate),
                checks=tuple(checks),
                check_logs=tuple(logs),
                browser=browser,
            )
            async with asyncio.timeout(180):
                review = ReviewResult.model_validate(await self.reviewer(context))
            bundle = VerificationBundle(
                candidate=candidate.ref,
                regression_on_base=checks[0],
                tests_on_candidate=checks[1],
                typecheck=checks[2],
                build=checks[3],
                browser=browser,
                review=review,
            )
            self.validate(bundle, candidate, session.require_current(candidate))
            await session.record_verification(candidate, bundle)
            return VerificationResult(
                status="passed",
                reason="All independent local gates passed; PR/CI not yet run",
                bundle=bundle,
                checks=tuple(checks),
                browser=browser,
                review=review,
            )
        except VerificationUnavailable as error:
            return VerificationResult(status="blocked", reason=str(error))
        except FixPolicyError as error:
            return VerificationResult(
                status="failed",
                reason=str(error),
                checks=tuple(checks),
                browser=browser,
                review=review,
            )
        except Exception:
            logger.exception("Private verification stage failed")
            return VerificationResult(
                status="blocked",
                reason="Verification infrastructure or injected stage failed",
                checks=tuple(checks),
                browser=browser,
                review=review,
            )

    def validate(
        self,
        bundle: VerificationBundle,
        candidate: FrozenCandidate,
        plan: ApprovedFixPlan,
    ) -> None:
        """Revalidate artifacts and all gates, including immediately before publication."""

        if isinstance(self.backend, DockerVerificationBackend):
            require_trusted_verification_runtime()
        validate_gates(bundle, candidate, plan)
        for check in (
            bundle.regression_on_base,
            bundle.tests_on_candidate,
            bundle.typecheck,
            bundle.build,
        ):
            self.evidence.validate(check.log, candidate.ref)
        assert bundle.browser.screenshot is not None
        self.evidence.validate(bundle.browser.screenshot, candidate.ref)
