"""Independent, fail-closed publication and CI orchestration for the private graph."""

import asyncio
import hashlib
import html
import io
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from PIL import Image
from pydantic import ValidationError

from tech_support_agent.bug_resolution.publication_contracts import (
    CIObservation,
    EvidenceExport,
    EvidenceFile,
    GitHubPort,
    GitPort,
    PreparedCommit,
    PublicationAuthority,
    PublicationError,
    PublicationPending,
    PublicationRef,
    PublisherConfig,
    PullRequest,
    VerifiedExport,
    WorkflowRun,
    manifest_digest,
)


def can_publish(authority: PublicationAuthority, export: VerifiedExport) -> None:
    """Require current approval and every real, candidate-bound local result.

    Args:
        authority: Current coordinator-owned case view, captured under its lock.
        export: Frozen Phase 4 export, not model-generated success claims.
    Raises:
        PublicationError: Approval, attempt, digest, or local evidence is stale.
    """

    authority = PublicationAuthority.model_validate(authority)
    export = VerifiedExport.model_validate(export)
    candidate = export.candidate
    if (
        authority.case_id != candidate.case_id
        or authority.attempt_count != candidate.attempt
        or authority.plan_revision != candidate.plan_revision
        or authority.approved_revision != candidate.plan_revision
        or authority.approved_by is None
        or authority.investigation != "confirmed"
        or authority.phase not in ("verified", "waiting_ci")
    ):
        raise PublicationError("Current confirmed case approval/attempt is required")
    if manifest_digest(export.files) != candidate.digest:
        raise PublicationError("Frozen source/test digest changed")
    expected = {
        "regression_on_base": "expected_assertion_failure",
        "tests": "passed",
        "build": "passed",
        "browser": "passed",
        "review": "passed",
    }
    if len(export.gates) != len(expected) or {
        gate.kind for gate in export.gates
    } != set(expected):
        raise PublicationError("Every independent local verification gate is required")
    for gate in export.gates:
        if gate.candidate != candidate or gate.outcome != expected[gate.kind]:
            raise PublicationError(
                "Local verification is stale, failed, or infrastructure-only"
            )
    if export.evidence.candidate != candidate:
        raise PublicationError(
            "Screenshot/report evidence belongs to another candidate"
        )
    if not export.regression_path.startswith(
        "tests/"
    ) or export.regression_path not in {file.path for file in export.files}:
        raise PublicationError("Frozen regression test is missing")


def _png(data: bytes) -> bytes:
    try:
        if not data.startswith(b"\x89PNG\r\n\x1a\n") or len(data) > 2_000_000:
            raise ValueError("PNG type/size")
        with Image.open(io.BytesIO(data)) as image:
            if (
                image.format != "PNG"
                or image.width * image.height > 4_000_000
                or getattr(image, "n_frames", 1) != 1
            ):
                raise ValueError("PNG dimensions/type")
            image.verify()
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            output = io.BytesIO()
            image.convert("RGB").save(output, format="PNG")
        result = output.getvalue()
        if len(result) > 2_000_000:
            raise ValueError("PNG size")
        return result
    except (OSError, ValueError, Image.DecompressionBombError) as error:
        raise PublicationError("Invalid evidence PNG") from error


def _text(value: str) -> str:
    return f"<pre>{html.escape(value).replace('@', '&#64;')}</pre>"


def case_marker(case_id: str) -> str:
    """Return a deterministic marker used in every reconciliation query."""

    return f"<!-- bug-resolution-case:{case_id} -->"


def report(export: VerifiedExport) -> str:
    """Render factual local results, with untrusted prose escaped as literal text."""

    candidate = export.candidate
    evidence = export.evidence
    sections = [
        case_marker(candidate.case_id),
        "# Verified candidate",
        f"Case: `{candidate.case_id}`; attempt: {candidate.attempt}; plan revision: {candidate.plan_revision}",
        f"Base SHA: `{candidate.base_sha}`",
        f"Candidate digest: `{candidate.digest}`",
    ]
    if candidate.snapshot_digest:
        sections.append(f"Upstream full snapshot digest: `{candidate.snapshot_digest}`")
    for title, text in (
        ("Problem", evidence.problem),
        ("Cause", evidence.cause),
        ("Approved behavior", evidence.approved_behavior),
        ("Changes", evidence.changes),
    ):
        sections.extend((f"## {title}", _text(text)))
    sections.append("## Actual local verification")
    for gate in export.gates:
        sections.extend(
            (
                f"### {gate.kind}: {gate.outcome}",
                _text(gate.summary),
                _text(f"Evidence: {gate.evidence_id}"),
            )
        )
    sections.append(
        "This is a verified PR candidate, not a production fix. No merge or deployment is performed. GitHub CI is evaluated separately for the published head."
    )
    rendered = "\n\n".join(sections) + "\n"
    if len(rendered.encode("utf-8")) > 55_000:
        raise PublicationError(
            "Report exceeds GitHub body budget; shorten factual summaries"
        )
    return rendered


def export_evidence(export: VerifiedExport) -> EvidenceExport:
    """Validate PNGs and generate only fixed case/attempt-relative evidence paths."""

    prefix = f"docs/evidence/{export.candidate.case_id}/"
    files = {
        f"{prefix}attempt-{export.candidate.attempt}-before.png": _png(
            export.evidence.before_png
        ),
        f"{prefix}attempt-{export.candidate.attempt}-after.png": _png(
            export.evidence.after_png
        ),
        f"{prefix}report.md": report(export).encode("utf-8"),
    }
    return EvidenceExport(
        files=tuple(
            EvidenceFile(path=path, content=content) for path, content in files.items()
        )
    )


def pr_body(config: PublisherConfig, export: VerifiedExport, head_sha: str) -> str:
    """Pin before/after links to the committed version, never a mutable branch."""

    prefix = f"https://github.com/{config.repo}/blob/{head_sha}/docs/evidence/{export.candidate.case_id}"
    links = "\n\n".join(
        f"![{kind}]({prefix}/attempt-{export.candidate.attempt}-{kind}.png?raw=true)"
        for kind in ("before", "after")
    )
    return f"{report(export)}\nPublished head: `{head_sha}`\n\n{links}\n"


@dataclass
class _Operation:
    fingerprint: str
    previous: PublicationRef | None
    commit: PreparedCommit | None = None
    create_uncertain: bool = False
    publication: PublicationRef | None = None


class Publisher:
    """One worker-lifetime publication ledger; coordinator still owns fix attempts.

    Operations are serialized. Callers must serialize case decisions against
    publication too; this service never obtains approval or increments attempts.
    Restart recovery is intentionally outside the MVP contract.
    """

    def __init__(
        self, config: PublisherConfig, git: GitPort, github: GitHubPort
    ) -> None:
        self.config = PublisherConfig.model_validate(config)
        self.git = git
        self.github = github
        self._lock = asyncio.Lock()
        self._operations: dict[tuple[str, str, int], _Operation] = {}

    def _require_config(self) -> None:
        if not self.config.live_authorized:
            raise PublicationError(
                "Demo repository and explicit publication authorization are required"
            )

    def _check_ref(self, publication: PublicationRef) -> None:
        PublicationRef.model_validate(publication)
        if any(
            (
                publication.repo != self.config.repo,
                publication.base_branch != self.config.base_branch,
                publication.workflow_id != self.config.workflow_id,
                publication.workflow_path != self.config.workflow_path,
                publication.required_jobs != self.config.required_jobs,
            )
        ):
            raise PublicationError(
                "Publication does not belong to configured repository/workflow"
            )

    def _check_pr(
        self, pr: PullRequest, branch: str, base: str, head: str | None, case_id: str
    ) -> None:
        if (
            pr.repo != self.config.repo
            or pr.head_repo != self.config.repo
            or pr.branch != branch
            or pr.base_branch != self.config.base_branch
            or pr.base_sha != base
            or pr.state != "open"
            or (head is not None and pr.head_sha != head)
            or case_marker(case_id) not in pr.body
        ):
            raise PublicationError(
                "PR identity, base, head, state or case marker changed"
            )

    async def _find(self, branch: str) -> PullRequest | None:
        prs = await self.github.find_pr(branch)
        if len(prs) > 1:
            raise PublicationError("Multiple case PRs require manual reconciliation")
        return prs[0] if prs else None

    async def publish(
        self,
        authority: PublicationAuthority,
        export: VerifiedExport,
        previous: PublicationRef | None = None,
    ) -> PublicationRef:
        """Publish once or reconcile an interrupted publication without a new attempt."""

        async with self._lock:
            self._require_config()
            authority = PublicationAuthority.model_validate(authority)
            export = VerifiedExport.model_validate(export)
            if previous is not None:
                previous = PublicationRef.model_validate(previous)
            can_publish(authority, export)
            evidence = export_evidence(export)
            candidate = export.candidate
            branch = f"fix/{candidate.case_id}"
            if previous:
                self._check_ref(previous)
                if (
                    previous.run_id != authority.run_id
                    or previous.candidate.case_id != candidate.case_id
                    or previous.candidate.base_sha != candidate.base_sha
                    or previous.candidate.attempt > candidate.attempt
                ):
                    raise PublicationError(
                        "Previous publication belongs to a different case/base/attempt"
                    )
                if (
                    previous.candidate.attempt == candidate.attempt
                    and previous.candidate != candidate
                ):
                    raise PublicationError(
                        "A changed candidate requires a new counted fix attempt"
                    )
            fingerprint = hashlib.sha256(
                candidate.model_dump_json().encode()
                + b"".join(
                    file.path.encode() + hashlib.sha256(file.content).digest()
                    for file in sorted(evidence.files, key=lambda file: file.path)
                )
            ).hexdigest()
            key = (authority.run_id, candidate.case_id, candidate.attempt)
            operation = self._operations.get(key)
            if operation is None:
                if previous and previous.candidate == candidate:
                    raise PublicationError(
                        "Publication ledger is absent; restart requires manual reconciliation"
                    )
                operation = _Operation(fingerprint=fingerprint, previous=previous)
                self._operations[key] = operation
            if operation.fingerprint != fingerprint:
                raise PublicationError("A frozen attempt changed during publication")
            previous = operation.previous
            allowed_old = previous.head_sha if previous else None
            heads = await self.github.heads(branch)
            if heads.base_sha != candidate.base_sha:
                raise PublicationError("Base branch moved after investigation")
            allowed_heads = {allowed_old}
            if operation.commit:
                allowed_heads.add(operation.commit.head_sha)
            if heads.branch_sha not in allowed_heads:
                raise PublicationError("Fix branch changed externally")
            pr = await self._find(branch)
            if pr:
                self._check_pr(
                    pr, branch, candidate.base_sha, heads.branch_sha, candidate.case_id
                )
                if previous and pr.number != previous.pr_number:
                    raise PublicationError("Case PR number changed")
                if not pr.draft and operation.publication is None:
                    raise PublicationError(
                        "Expected a draft PR; external readiness requires review"
                    )
            elif previous:
                raise PublicationError("Previously published PR is missing")
            if operation.commit is None:
                operation.commit = await self.git.prepare(export, previous, evidence)
            commit = operation.commit
            can_publish(authority, export)
            await self.git.verify(commit, export)
            # Reconcile again after local work; never push over a moved remote.
            heads = await self.github.heads(branch)
            if heads.base_sha != candidate.base_sha or heads.branch_sha not in {
                allowed_old,
                commit.head_sha,
            }:
                raise PublicationError(
                    "Remote branch moved while preparing publication"
                )
            if heads.branch_sha != commit.head_sha:
                try:
                    await self.git.push(commit, branch)
                except PublicationPending:
                    pass
                heads = await self.github.heads(branch)
                if heads.base_sha != candidate.base_sha:
                    raise PublicationError("Base branch changed during push")
                if heads.branch_sha == allowed_old:
                    raise PublicationPending(
                        "Push not confirmed; retry will first reconcile"
                    )
                if heads.branch_sha != commit.head_sha:
                    raise PublicationError("Remote head is not the committed candidate")
            body = pr_body(self.config, export, commit.head_sha)
            pr = await self._find(branch)
            if pr is None:
                if operation.create_uncertain:
                    raise PublicationPending(
                        "PR creation remains uncertain; waiting for reconciliation"
                    )
                operation.create_uncertain = True
                try:
                    await self.github.create_pr(
                        branch, f"fix: verified bug {candidate.case_id}", body
                    )
                except PublicationPending:
                    pass
                pr = await self._find(branch)
                if pr is None:
                    raise PublicationPending(
                        "PR creation not confirmed; do not create blindly again"
                    )
            self._check_pr(
                pr, branch, candidate.base_sha, commit.head_sha, candidate.case_id
            )
            if previous and pr.number != previous.pr_number:
                raise PublicationError("Case PR changed during publication")
            if not pr.draft and operation.publication is None:
                raise PublicationError(
                    "Publication must remain draft pending exact-head CI"
                )
            if pr.body != body:
                try:
                    await self.github.update_body(pr.number, body)
                except PublicationPending:
                    pass
            pr = await self.github.get_pr(pr.number)
            self._check_pr(
                pr, branch, candidate.base_sha, commit.head_sha, candidate.case_id
            )
            if pr.body != body:
                raise PublicationPending(
                    "Current candidate report update is not confirmed"
                )
            publication = PublicationRef(
                run_id=authority.run_id,
                candidate=candidate,
                repo=self.config.repo,
                base_branch=self.config.base_branch,
                branch=branch,
                pr_number=pr.number,
                head_sha=commit.head_sha,
                workflow_id=self.config.workflow_id,
                workflow_path=self.config.workflow_path,
                required_jobs=self.config.required_jobs,
                pr_url=f"https://github.com/{self.config.repo}/pull/{pr.number}",
            )
            operation.publication = publication
            return publication

    async def inspect_ci(self, publication: PublicationRef) -> CIObservation:
        """Observe exact workflow/PR/head/jobs; transport and cancellation stay pending."""

        self._require_config()
        publication = PublicationRef.model_validate(publication)
        self._check_ref(publication)
        try:
            return await self._inspect_ci(publication)
        except PublicationPending:
            return CIObservation(
                publication=publication,
                status="pending",
                reason="GitHub transport unavailable; retry observation without spending an attempt",
            )
        except ValidationError, KeyError, TypeError, ValueError:
            return CIObservation(
                publication=publication,
                status="pending",
                reason="GitHub response could not be verified",
            )
        except PublicationError as error:
            return CIObservation(
                publication=publication, status="blocked", reason=str(error)
            )

    async def _inspect_ci(self, publication: PublicationRef) -> CIObservation:
        def result(
            status: str, reason: str, run: WorkflowRun | None = None
        ) -> CIObservation:
            return CIObservation(
                publication=publication,
                status=status,
                reason=reason,
                run_id=run.id if run else None,
                run_attempt=run.run_attempt if run else None,
            )

        pr = await self.github.get_pr(publication.pr_number)
        self._check_pr(
            pr,
            publication.branch,
            publication.candidate.base_sha,
            publication.head_sha,
            publication.candidate.case_id,
        )
        heads = await self.github.heads(publication.branch)
        if (
            heads.base_sha != publication.candidate.base_sha
            or heads.branch_sha != publication.head_sha
        ):
            return result("blocked", "Remote base or head changed")
        workflow = await self.github.workflow()
        if (
            workflow.id != publication.workflow_id
            or workflow.path != publication.workflow_path
            or workflow.state != "active"
        ):
            return result(
                "blocked", "Expected workflow is missing, disabled, or changed"
            )
        valid_shas = {publication.head_sha}
        if pr.merge_sha:
            parents = await self.github.commit_parents(pr.merge_sha)
            if parents == (publication.candidate.base_sha, publication.head_sha):
                valid_shas.add(pr.merge_sha)
        runs = [
            run
            for run in await self.github.runs()
            if (
                run.workflow_id == publication.workflow_id
                and run.path == publication.workflow_path
                and run.repo == publication.repo
                and run.head_repo == publication.repo
                and run.event == "pull_request"
                and run.head_branch == publication.branch
                and run.head_sha in valid_shas
                and any(
                    link.number == publication.pr_number
                    and link.head_sha == publication.head_sha
                    and link.base_sha == publication.candidate.base_sha
                    for link in run.pull_requests
                )
            )
        ]
        if not runs:
            return result("pending", "No exact workflow/PR/head run yet")
        run = max(runs, key=lambda run: (run.run_number, run.run_attempt, run.id))
        if run.status != "completed" or run.conclusion in (
            None,
            "cancelled",
            "skipped",
            "neutral",
            "stale",
            "action_required",
        ):
            return result("pending", "Workflow has not completed successfully", run)
        jobs = await self.github.jobs(run)
        required = [job for job in jobs if job.name in publication.required_jobs]
        if len(required) != len(publication.required_jobs) or {
            job.name for job in required
        } != set(publication.required_jobs):
            return result("pending", "Required jobs missing or ambiguous", run)
        if any(
            job.run_id != run.id
            or job.run_attempt != run.run_attempt
            or job.head_sha not in valid_shas
            for job in jobs
        ):
            return result(
                "pending", "Jobs do not belong to this exact run attempt/head", run
            )
        if any(
            job.status != "completed"
            or job.conclusion
            in (None, "cancelled", "skipped", "neutral", "stale", "action_required")
            for job in required
        ):
            return result(
                "pending", "Required jobs are incomplete, skipped or cancelled", run
            )
        if run.conclusion in ("failure", "timed_out", "startup_failure") or any(
            job.conclusion in ("failure", "timed_out", "startup_failure")
            for job in required
        ):
            return result(
                "failed",
                "Exact-head CI failed; use the existing global attempt budget",
                run,
            )
        if run.conclusion != "success" or any(
            job.conclusion != "success" for job in required
        ):
            return result("pending", "Unknown CI conclusion is not success", run)
        current = await self.github.get_pr(publication.pr_number)
        self._check_pr(
            current,
            publication.branch,
            publication.candidate.base_sha,
            publication.head_sha,
            publication.candidate.case_id,
        )
        return result(
            "passed",
            "Expected workflow and required jobs passed for the current PR head",
            run,
        )

    async def mark_ready(
        self,
        authority: PublicationAuthority,
        export: VerifiedExport,
        publication: PublicationRef,
    ) -> CIObservation:
        """Recheck local authority and CI immediately before and after readiness."""

        async with self._lock:
            authority = PublicationAuthority.model_validate(authority)
            export = VerifiedExport.model_validate(export)
            publication = PublicationRef.model_validate(publication)
            can_publish(authority, export)
            if (
                authority.run_id != publication.run_id
                or export.candidate != publication.candidate
            ):
                raise PublicationError("Stale readiness request")
            observation = await self.inspect_ci(publication)
            if observation.status != "passed":
                return observation
            async with self._readiness_guard(publication):
                return await self._complete_readiness(publication, observation)

    @asynccontextmanager
    async def _readiness_guard(
        self, publication: PublicationRef
    ) -> AsyncIterator[None]:
        try:
            yield
        except asyncio.CancelledError:

            async def restore() -> None:
                try:
                    async with asyncio.timeout(10):
                        result = await self._restore_draft(publication)
                    logging.getLogger(__name__).warning(result.reason)
                except Exception:  # noqa: BLE001 - Cleanup must preserve the original cancellation.
                    logging.getLogger(__name__).error(
                        "Cancelled readiness: draft restoration unconfirmed; operator intervention required"
                    )

            cleanup = asyncio.create_task(restore())
            while not cleanup.done():
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    continue
            raise

    async def _complete_readiness(
        self, publication: PublicationRef, observation: CIObservation
    ) -> CIObservation:
        try:
            await self.github.set_ready(publication.pr_number, ready=True)
        except PublicationPending:
            pass
        # GitHub offers no expected-head CAS on draft toggles. Recheck and
        # return to draft if CI/head changed during the readiness mutation.
        after = await self.inspect_ci(publication)
        if (
            after.status != "passed"
            or after.run_id != observation.run_id
            or after.run_attempt != observation.run_attempt
        ):
            return await self._restore_draft(publication)
        try:
            pr = await self.github.get_pr(publication.pr_number)
            self._check_pr(
                pr,
                publication.branch,
                publication.candidate.base_sha,
                publication.head_sha,
                publication.candidate.case_id,
            )
        except PublicationError, ValidationError, ValueError, KeyError, TypeError:
            return await self._restore_draft(publication)
        if pr.draft:
            return CIObservation(
                publication=publication,
                status="pending",
                reason="Readiness mutation is not confirmed",
            )
        return after

    async def _restore_draft(self, publication: PublicationRef) -> CIObservation:
        try:
            try:
                await self.github.set_ready(publication.pr_number, ready=False)
            except PublicationPending:
                pass
            pr = await self.github.get_pr(publication.pr_number)
            if not pr.draft:
                raise PublicationPending("Draft restoration not confirmed")
        except PublicationError, ValidationError, ValueError, KeyError, TypeError:
            return CIObservation(
                publication=publication,
                status="blocked",
                reason="Readiness race: draft restoration unconfirmed; operator intervention required",
            )
        return CIObservation(
            publication=publication,
            status="blocked",
            reason="Head or CI changed during readiness; PR returned to draft",
        )
