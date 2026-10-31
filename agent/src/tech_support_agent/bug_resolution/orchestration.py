"""Trusted bridge between approvals, one fix ledger, verification and publication."""

import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from io import BytesIO
from typing import Protocol

from PIL import Image

from tech_support_agent.bug_resolution.adapters.sandbox import ImageArtifact
from tech_support_agent.bug_resolution.contracts import (
    CaseSnapshot,
    ExecutionOutcome,
    InvestigationResult,
)
from tech_support_agent.bug_resolution.fix_contracts import (
    REGRESSION_PATH,
    ApprovedFixPlan,
    FixPolicyError,
    FixResult,
)
from tech_support_agent.bug_resolution.fixing import (
    EditableSandbox,
    FixSession,
    ScopedFixTools,
)
from tech_support_agent.bug_resolution.nodes.fix import FixNode, FixRequest
from tech_support_agent.bug_resolution.publication import Publisher
from tech_support_agent.bug_resolution.publication_contracts import (
    CandidateIdentity,
    CIObservation,
    ExportFile,
    LocalGate,
    PublicationAuthority,
    PublicationError,
    PublicationEvidence,
    PublicationPending,
    PublicationRef,
    VerifiedExport,
    manifest_digest,
)
from tech_support_agent.bug_resolution.verification import (
    CandidateVerifier,
    candidate_diff,
)


class FeedbackFixer(Protocol):
    async def __call__(
        self, plan: ApprovedFixPlan, tools: ScopedFixTools, feedback: str
    ) -> FixResult: ...


@dataclass
class CaseExecution:
    """Worker-owned mutable resources, never a model or checkpoint payload."""

    session: FixSession
    node: FixNode
    feedback: str = ""
    fix_summary: str = ""
    export: VerifiedExport | None = None


@dataclass(frozen=True)
class Reproduction:
    run_id: str
    base_sha: str
    artifact: ImageArtifact
    sha256: str


def approved_fix_plan(case: CaseSnapshot) -> ApprovedFixPlan:
    """Project only a current, confirmed coordinator-approved plan into edit authority."""

    approval = case.approval
    if (
        approval is None
        or approval.revision != case.plan_revision
        or case.base_sha is None
        or case.investigation is None
        or case.investigation.status != "confirmed"
        or case.plan is None
        or approval.expected_behavior != case.plan.expected_behavior
        or approval.allowed_changes != case.plan.allowed_changes
        or approval.acceptance_checks != case.plan.acceptance_checks
    ):
        raise FixPolicyError("Current confirmed approval required")
    return ApprovedFixPlan(
        run_id=case.run_id,
        case_id=case.case_id,
        base_sha=case.base_sha,
        revision=approval.revision,
        expected_behavior=approval.expected_behavior,
        allowed_changes=tuple(approval.allowed_changes),
        acceptance_checks=tuple(approval.acceptance_checks),
        approved_by=approval.approved_by,
    )


def terminal(
    case: CaseSnapshot, reason: str, *, completed: bool = False
) -> CaseSnapshot:
    """Preserve factual attempt/PR state without claiming deployment or fabricated success."""

    publication = (
        f"\nPR: {case.publication.pr_url}\nHead: {case.publication.head_sha}"
        if case.publication
        else ""
    )
    return case.model_copy(
        update={
            "workflow_route": "finish",
            "outcome": ExecutionOutcome(
                status="completed" if completed else "blocked",
                summary=f"{reason[:2800]}\nFix attempts used: {case.attempt_count}/3.{publication}\nNo merge or production deployment was performed.",
            ),
        }
    )


class ResolutionWorkflow:
    """One case-local ledger survives local failures, CI failures and reapproval."""

    def __init__(
        self,
        factory: Callable[[], Awaitable[EditableSandbox]],
        fixer: FeedbackFixer,
        verifier: CandidateVerifier,
        publisher: Publisher | None,
        artifacts: dict[str, ImageArtifact],
        *,
        max_tools: int = 30,
        max_seconds: float = 300,
        poll_interval: float = 10,
    ) -> None:
        if poll_interval <= 0:
            raise ValueError("Positive CI poll interval required")
        self.factory, self.fixer, self.verifier, self.publisher = (
            factory,
            fixer,
            verifier,
            publisher,
        )
        self.artifacts = artifacts
        self.max_tools, self.max_seconds, self.poll_interval = (
            max_tools,
            max_seconds,
            poll_interval,
        )
        self.cases: dict[str, CaseExecution] = {}
        self.reproductions: dict[str, Reproduction] = {}

    def register_reproduction(
        self, case: CaseSnapshot, result: InvestigationResult
    ) -> None:
        """Bind a real investigator screenshot to the case/run/base before any edits."""

        if result.status != "confirmed":
            return
        artifact = self.artifacts[result.artifact_ids[0]]
        data = self._image_bytes(artifact)
        if case.base_sha is None:
            raise FixPolicyError("Reproduction base is absent")
        self.reproductions[case.case_id] = Reproduction(
            case.run_id, case.base_sha, artifact, hashlib.sha256(data).hexdigest()
        )

    def _image_bytes(self, artifact: ImageArtifact) -> bytes:
        path = artifact.path
        if (
            path.parent != self.verifier.evidence.directory
            or path.is_symlink()
            or not path.is_file()
            or path.stat().st_size != artifact.size
            or artifact.size > 2_000_000
        ):
            raise FixPolicyError("Untrusted reproduction artifact")
        data = path.read_bytes()
        with Image.open(BytesIO(data)) as image:
            if (
                image.format != "PNG"
                or image.size != (artifact.width, artifact.height)
                or image.width * image.height > 4_000_000
            ):
                raise FixPolicyError("Invalid reproduction PNG")
            image.verify()
        return data

    def _execution(self, case: CaseSnapshot) -> CaseExecution:
        execution = self.cases.get(case.case_id)
        if execution is None:
            plan = approved_fix_plan(case)
            if case.attempt_count:
                raise FixPolicyError(
                    "Attempt ledger absent; restart requires manual reconciliation"
                )
            session = FixSession(
                plan.run_id,
                plan.case_id,
                plan.base_sha,
                self.factory,
                max_tools=self.max_tools,
                max_seconds=self.max_seconds,
            )

            async def fix(plan: ApprovedFixPlan, tools: ScopedFixTools) -> FixResult:
                return await self.fixer(plan, tools, execution.feedback)

            execution = CaseExecution(session, FixNode(session, fix))
            self.cases[case.case_id] = execution
        if (
            execution.session.run_id != case.run_id
            or execution.session.base_sha != case.base_sha
        ):
            raise FixPolicyError("Case ledger identity changed")
        return execution

    async def fix(self, case: CaseSnapshot) -> CaseSnapshot:
        """Install trusted approval without resetting attempts, then freeze one scoped edit."""

        execution = self._execution(case)
        await execution.session.install_approval(approved_fix_plan(case))
        result = await execution.node(
            FixRequest(
                operation_id=f"attempt-{case.attempt_count + 1}",
                plan_revision=case.plan_revision,
            )
        )
        execution.fix_summary = result.summary
        return case.model_copy(
            update={
                "phase": "fixing",
                "attempt_count": execution.session.attempts_used,
                "workflow_route": "verify" if result.candidate else "retry",
                "retry_reason": result.summary,
                "retry_new_approach": result.status == "needs_reapproval",
                "ci_observation": None,
                "outcome": None,
            }
        )

    async def verify(self, case: CaseSnapshot) -> CaseSnapshot:
        """Verify the current frozen snapshot through independent checks, browser and review."""

        execution = self._execution(case)
        candidate = execution.session.candidate
        if candidate is None:
            raise FixPolicyError("No frozen candidate")
        result = await self.verifier.verify(execution.session, candidate)
        if result.status != "passed":
            logs = []
            for check in result.checks:
                self.verifier.evidence.validate(check.log, candidate.ref)
                logs.append(
                    (
                        self.verifier.evidence.directory
                        / f"{check.log.artifact_id}.log"
                    ).read_text()[:12000]
                )
            execution.feedback = (
                f"Failure: {result.reason}\nReview: {result.review.model_dump_json() if result.review else 'not reached'}\n"
                f"Browser: {result.browser.model_dump_json() if result.browser else 'not reached'}\n"
                + "\n".join(logs)
                + "\nPrevious attempted diff:\n"
                + candidate_diff(candidate)[:100000]
            )
            return case.model_copy(
                update={
                    "workflow_route": "retry",
                    "retry_reason": result.reason,
                    "retry_new_approach": bool(
                        result.review and not result.review.scope_matches_plan
                    ),
                }
            )
        screenshot = result.bundle.browser.screenshot
        path = self.verifier.evidence.directory / f"{screenshot.artifact_id}.png"
        with Image.open(path) as image:
            artifact = ImageArtifact(
                artifact_id=screenshot.artifact_id,
                path=path,
                size=screenshot.size,
                width=image.width,
                height=image.height,
            )
        self.artifacts[artifact.artifact_id] = artifact
        return case.model_copy(
            update={
                "phase": "verified",
                "workflow_route": "publish",
                "after_artifact_id": artifact.artifact_id,
            }
        )

    def export(self, case: CaseSnapshot) -> VerifiedExport:
        """Revalidate full frozen bytes and real evidence before mapping publication gates."""

        execution = self._execution(case)
        session = execution.session
        candidate, bundle = session.candidate, session.verification
        if (
            candidate is None
            or bundle is None
            or session.attempts_used != case.attempt_count
            or session.require_current(candidate) != approved_fix_plan(case)
        ):
            raise FixPolicyError("Current fully verified candidate required")
        self.verifier.validate(bundle, candidate, session.approval)
        before = self.reproductions.get(case.case_id)
        if (
            before is None
            or before.run_id != case.run_id
            or before.base_sha != case.base_sha
            or before.artifact.artifact_id not in case.investigation.artifact_ids
        ):
            raise FixPolicyError("Missing case-bound reproduction evidence")
        before_png = self._image_bytes(before.artifact)
        if hashlib.sha256(before_png).hexdigest() != before.sha256:
            raise FixPolicyError("Reproduction evidence changed")
        if execution.export is not None:
            if execution.export.candidate.snapshot_digest != candidate.ref.digest:
                raise FixPolicyError("Cached export no longer matches candidate")
            return execution.export
        files = tuple(
            ExportFile(path=file.path, content=file.content.encode())
            for file in candidate.files
            if file.path.startswith(("src/", "tests/"))
        )
        identity = CandidateIdentity(
            case_id=case.case_id,
            attempt=case.attempt_count,
            plan_revision=case.plan_revision,
            base_sha=case.base_sha,
            digest=manifest_digest(files),
            snapshot_digest=candidate.ref.digest,
        )
        review_log = self.verifier.evidence.record_log(
            candidate.ref, bundle.review.model_dump_json()
        )
        gates = [
            LocalGate(
                candidate=identity,
                kind=kind,
                outcome=outcome,
                evidence_id=artifact_id,
                summary=summary[:8000],
            )
            for kind, outcome, artifact_id, summary in (
                (
                    "regression_on_base",
                    "expected_assertion_failure",
                    bundle.regression_on_base.log.artifact_id,
                    bundle.regression_on_base.assertion.message,
                ),
                (
                    "tests",
                    "passed",
                    bundle.tests_on_candidate.log.artifact_id,
                    "All three existing smoke tests and the new regression passed without skips.",
                ),
                (
                    "build",
                    "passed",
                    bundle.build.log.artifact_id,
                    f"Build and typecheck exited zero. Typecheck evidence: {bundle.typecheck.log.artifact_id}",
                ),
                (
                    "browser",
                    "passed",
                    bundle.browser.screenshot.artifact_id,
                    bundle.browser.observation,
                ),
                ("review", "passed", review_log.artifact_id, bundle.review.summary),
            )
        ]
        after_png = (
            self.verifier.evidence.directory
            / f"{bundle.browser.screenshot.artifact_id}.png"
        ).read_bytes()
        execution.export = VerifiedExport(
            candidate=identity,
            files=files,
            regression_path=REGRESSION_PATH,
            gates=tuple(gates),
            evidence=PublicationEvidence(
                candidate=identity,
                before_png=before_png,
                after_png=after_png,
                problem=case.request.report[:8000],
                cause=case.investigation.summary[:8000],
                approved_behavior=case.approval.expected_behavior[:8000],
                changes=execution.fix_summary[:8000],
            ),
        )
        return execution.export

    async def publish(
        self, case: CaseSnapshot, authority: PublicationAuthority
    ) -> CaseSnapshot:
        """Publish or reconcile the same immutable attempt; never retry a fix for transport."""

        if self.publisher is None:
            return terminal(
                case,
                "All independent local gates passed. Publication is disabled: configure the explicit GitHub target, credentials and live authorization. No ready PR is claimed.",
            )
        try:
            publication = await self.publisher.publish(
                authority, self.export(case), case.publication
            )
        except PublicationPending:
            return case.model_copy(update={"workflow_route": "wait_publication"})
        except PublicationError, FixPolicyError:
            return terminal(
                case,
                "Publication could not be safely verified. Inspect remote state and evidence manually; no ready PR is claimed.",
            )
        return case.model_copy(
            update={
                "phase": "waiting_ci",
                "publication": publication,
                "workflow_route": "wait_ci",
                "ci_observation": None,
            }
        )

    async def inspect_ci(self, publication: PublicationRef) -> CIObservation:
        """Observe exact-head CI through the injected publisher, without invoking a model."""

        if self.publisher is None:
            raise PublicationError("Publisher unavailable")
        return await self.publisher.inspect_ci(publication)

    async def mark_ready(
        self, case: CaseSnapshot, authority: PublicationAuthority
    ) -> CaseSnapshot:
        """Revalidate evidence and current CI around the readiness side effect."""

        if self.publisher is None or case.publication is None:
            raise PublicationError("No publication to mark ready")
        try:
            observation = await self.publisher.mark_ready(
                authority, self.export(case), case.publication
            )
        except PublicationPending:
            observation = CIObservation(
                publication=case.publication,
                status="pending",
                reason="Readiness transport is uncertain; waiting for reconciliation",
            )
        except PublicationError, FixPolicyError:
            return terminal(
                case,
                "Readiness verification failed. Operator inspection is required; no ready PR is claimed.",
            )
        case = self.observe(case, observation)
        if observation.status == "passed":
            return terminal(
                case,
                "Verified fix candidate is ready for review. Regression red on base; regression and three smoke tests green on candidate; typecheck, build, independent browser verification and review passed. Exact-head GitHub CI passed and PR readiness was confirmed.",
                completed=True,
            )
        return case

    def observe(self, case: CaseSnapshot, observation: CIObservation) -> CaseSnapshot:
        """Route only a matching CI observation; pending never consumes an attempt."""

        if observation.publication != case.publication:
            raise PublicationError("Stale CI observation")
        case = case.model_copy(update={"ci_observation": observation})
        if observation.status == "blocked":
            return terminal(
                case, f"CI is blocked: {observation.reason}. No ready PR is claimed."
            )
        if observation.status == "failed":
            execution = self._execution(case)
            execution.feedback = f"{observation.reason}\nPrevious attempted diff:\n{candidate_diff(execution.session.candidate)[:100000]}"
        return case.model_copy(
            update={
                "workflow_route": {
                    "pending": "wait_ci",
                    "passed": "mark_ready",
                    "failed": "retry",
                }[observation.status],
                "retry_reason": observation.reason,
                "retry_new_approach": False,
            }
        )

    async def retry(self, case: CaseSnapshot) -> CaseSnapshot:
        """Revoke stale evidence and preserve the same three-attempt ledger on every path."""

        execution = self._execution(case)
        decision = await execution.session.retry_or_handoff(
            case.retry_reason, new_approach=case.retry_new_approach
        )
        execution.export = None
        if not execution.feedback:
            execution.feedback = case.retry_reason
        case = case.model_copy(
            update={
                "attempt_count": decision.attempts_used,
                "ci_observation": None,
                "after_artifact_id": None,
                "outcome": None,
            }
        )
        if decision.action == "handoff":
            return terminal(
                case,
                f"Manual handoff: the global three-attempt budget is exhausted. Last failure: {case.retry_reason}. No ready PR is claimed.",
            )
        return case.model_copy(
            update={
                "workflow_route": "reapprove"
                if decision.action == "reapprove"
                else "fix",
                "approval": None if decision.action == "reapprove" else case.approval,
                "team_event": None,
            }
        )

    async def cleanup(self, case_id: str) -> None:
        """Revoke outstanding tools and retain the spent ledger for audit, not resumption."""

        if execution := self.cases.get(case_id):
            await execution.session.close()

    async def close(self) -> None:
        """Close every case-local editing capability on worker shutdown."""

        for case_id in self.cases:
            await self.cleanup(case_id)
