import base64
import io
from pathlib import Path
from uuid import uuid4

import pytest
from PIL import Image

from tech_support_agent.bug_resolution.evidence import EvidenceStore
from tech_support_agent.bug_resolution.fix_contracts import (
    AssertionFailure,
    BrowserEvidence,
    CheckEvidence,
    FixPolicyError,
    ReviewResult,
    TestObservation,
    TestReport,
    VerificationBundle,
)
from tech_support_agent.bug_resolution.sandbox.protocol import Screenshot
from tech_support_agent.bug_resolution.verification import (
    CandidateVerifier,
    expected_red,
    regression_hash,
    validate_gates,
)
from tests.bug_resolution.fix_fixtures import edit_and_freeze, plan, session

TestObservation.__test__ = False
TestReport.__test__ = False


def red_report() -> TestReport:
    assertion = AssertionFailure(
        title="expect.toBeEnabled",
        file="tests/regression.spec.ts",
        line=8,
        message="Expected enabled, received disabled",
    )
    return TestReport(
        tests=(
            TestObservation(
                title="Continue progresses",
                file=assertion.file,
                status="failed",
                expected_status="passed",
                assertions=(assertion,),
                errors=(assertion.message,),
            ),
        )
    )


def green_report() -> TestReport:
    return TestReport(
        tests=tuple(
            TestObservation(
                title=f"test {i}",
                file="tests/regression.spec.ts" if i == 0 else "tests/smoke.spec.ts",
                status="passed",
                expected_status="passed",
            )
            for i in range(4)
        )
    )


def bundle_for(candidate, store: EvidenceStore) -> VerificationBundle:
    ref = candidate.ref
    red = red_report()
    checks = []
    for kind in ("regression_on_base", "tests", "typecheck", "build"):
        checks.append(
            CheckEvidence(
                candidate=ref,
                kind=kind,
                status="passed",
                exit_code=1 if kind == "regression_on_base" else 0,
                log=store.record_log(ref, f"actual fixture log {kind}"),
                report=red
                if kind == "regression_on_base"
                else green_report()
                if kind == "tests"
                else None,
                assertion=red.tests[0].assertions[0]
                if kind == "regression_on_base"
                else None,
                regression_sha256=regression_hash(candidate)
                if kind in ("regression_on_base", "tests")
                else None,
            )
        )
    data = io.BytesIO()
    Image.new("RGB", (2, 2), "red").save(data, "PNG")
    png = store.record_png(
        ref,
        Screenshot(
            artifact_id=uuid4().hex,
            mime="image/png",
            data=base64.b64encode(data.getvalue()).decode(),
        ),
    )
    return VerificationBundle(
        candidate=ref,
        regression_on_base=checks[0],
        tests_on_candidate=checks[1],
        typecheck=checks[2],
        build=checks[3],
        browser=BrowserEvidence(
            candidate=ref,
            status="passed",
            checked_acceptance=plan().acceptance_checks,
            observation="Observed transition in test fixture",
            screenshot=png,
        ),
        review=ReviewResult(
            candidate=ref,
            blocking_findings=(),
            scope_matches_plan=True,
            regression_is_behavioral=True,
            regression_not_weakened=True,
            summary="Fixture review",
        ),
    )


@pytest.mark.parametrize(
    "failure",
    [
        "syntax",
        "dependency",
        "timeout",
        "skip",
        "expected_failure",
        "weak",
        "other_file",
        "extra_error",
        "no_tests",
    ],
)
def test_infrastructure_and_weak_failures_are_not_red(failure: str) -> None:
    report = red_report()
    test = report.tests[0]
    if failure in ("syntax", "dependency"):
        report = report.model_copy(
            update={
                "errors": (
                    "SyntaxError"
                    if failure == "syntax"
                    else "Cannot find module helper",
                )
            }
        )
    elif failure == "no_tests":
        report = TestReport(tests=())
    else:
        changes = {
            "timeout": {"status": "timedOut"},
            "skip": {"status": "skipped"},
            "expected_failure": {"expected_status": "failed"},
            "weak": {
                "assertions": (
                    test.assertions[0].model_copy(update={"title": "expect.toBe"}),
                )
            },
            "other_file": {"file": "tests/smoke.spec.ts"},
            "extra_error": {
                "errors": (*test.errors, "browser crashed during teardown")
            },
        }[failure]
        report = report.model_copy(update={"tests": (test.model_copy(update=changes),)})
    assert not expected_red(report)


@pytest.mark.parametrize(
    "gate",
    [
        "red",
        "tests",
        "typecheck",
        "build",
        "browser",
        "screenshot",
        "review",
        "weak_test",
        "scope",
        "stale_candidate",
        "stale_revision",
        "digest",
        "different_test",
        "skipped_test",
        "partial_browser",
    ],
)
async def test_every_gate_is_required(tmp_path: Path, gate: str) -> None:
    subject, _ = session()
    await subject.install_approval(plan())
    candidate = await edit_and_freeze(subject)
    bundle = bundle_for(candidate, EvidenceStore(tmp_path))
    approved = plan()
    if gate in ("red", "tests", "typecheck", "build"):
        field = {"red": "regression_on_base", "tests": "tests_on_candidate"}.get(
            gate, gate
        )
        bundle = bundle.model_copy(
            update={
                field: getattr(bundle, field).model_copy(update={"status": "failed"})
            }
        )
    elif gate in ("browser", "screenshot", "partial_browser"):
        changes = {
            "browser": {"status": "failed"},
            "screenshot": {"screenshot": None},
            "partial_browser": {"checked_acceptance": ("Continue is enabled",)},
        }[gate]
        bundle = bundle.model_copy(
            update={"browser": bundle.browser.model_copy(update=changes)}
        )
    elif gate in ("review", "weak_test", "scope"):
        changes = {
            "review": {"blocking_findings": ("Bug remains",)},
            "weak_test": {"regression_is_behavioral": False},
            "scope": {"scope_matches_plan": False},
        }[gate]
        bundle = bundle.model_copy(
            update={"review": bundle.review.model_copy(update=changes)}
        )
    elif gate == "stale_candidate":
        bundle = bundle.model_copy(
            update={"candidate": bundle.candidate.model_copy(update={"attempt": 2})}
        )
    elif gate == "stale_revision":
        approved = plan(2)
    elif gate == "digest":
        candidate = candidate.model_copy(
            update={"ref": candidate.ref.model_copy(update={"digest": "b" * 64})}
        )
    elif gate == "different_test":
        bundle = bundle.model_copy(
            update={
                "tests_on_candidate": bundle.tests_on_candidate.model_copy(
                    update={"regression_sha256": "b" * 64}
                )
            }
        )
    else:
        report = bundle.tests_on_candidate.report
        skipped = report.tests[0].model_copy(update={"status": "skipped"})
        report = report.model_copy(update={"tests": (skipped, *report.tests[1:])})
        bundle = bundle.model_copy(
            update={
                "tests_on_candidate": bundle.tests_on_candidate.model_copy(
                    update={"report": report}
                )
            }
        )
    with pytest.raises(FixPolicyError):
        validate_gates(bundle, candidate, approved)


async def test_real_evidence_integrity_and_complete_verifier(tmp_path: Path) -> None:
    subject, _ = session()
    await subject.install_approval(plan())
    candidate = await edit_and_freeze(subject)
    store = EvidenceStore(tmp_path)
    bundle = bundle_for(candidate, store)

    class Backend:
        async def check(self, candidate, kind):
            return getattr(bundle, {"tests": "tests_on_candidate"}.get(kind, kind))

        async def browser(self, candidate, approved):
            return bundle.browser

    contexts = []

    async def reviewer(context):
        contexts.append(context)
        return bundle.review

    verifier = CandidateVerifier(Backend(), store, reviewer)
    result = await verifier.verify(subject, candidate)
    assert result.status == "passed" and subject.verification == bundle
    assert contexts[0].diff and len(contexts[0].check_logs) == 4
    assert "messages" not in type(contexts[0]).model_fields
    png = bundle.browser.screenshot
    (tmp_path / f"{png.artifact_id}.png").write_bytes(b"modified")
    with pytest.raises(FixPolicyError, match="changed"):
        verifier.validate(bundle, candidate, plan())
    await subject.retry_or_handoff("new approach", new_approach=True)
    assert subject.verification is None
    assert (await verifier.verify(subject, candidate)).status != "passed"


async def test_fabricated_artifact_and_backend_failure_never_pass(
    tmp_path: Path,
) -> None:
    subject, _ = session()
    await subject.install_approval(plan())
    candidate = await edit_and_freeze(subject)
    store = EvidenceStore(tmp_path)
    bundle = bundle_for(candidate, store)
    with pytest.raises(FixPolicyError):
        store.validate(
            bundle.build.log.model_copy(update={"artifact_id": uuid4().hex}),
            candidate.ref,
        )

    class Broken:
        async def check(self, candidate, kind):
            raise OSError("Docker unavailable")

    async def never(context):
        pytest.fail("review must not run after infrastructure failure")

    assert (
        await CandidateVerifier(Broken(), store, never).verify(subject, candidate)
    ).status == "blocked"
    assert subject.verification is None
