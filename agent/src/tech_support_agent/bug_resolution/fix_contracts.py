"""Isolated Phase 4 contracts; approvals enter only from trusted worker code."""

import hashlib
import json
from typing import Literal

from pydantic import Field, field_validator, model_validator

from tech_support_agent.bug_resolution.sandbox.protocol import (
    SourceFile,
    WireModel,
    safe_path,
)

REGRESSION_PATH = "tests/regression.spec.ts"


class FixPolicyError(RuntimeError):
    """Permission, scope, freshness, or evidence validation failed closed."""


class ApprovedFixPlan(WireModel):
    """Trusted projection of a consumed approval, never a model output schema."""

    run_id: str = Field(min_length=1, max_length=100)
    case_id: str = Field(min_length=1, max_length=100)
    revision: int = Field(ge=1)
    base_sha: str = Field(pattern=r"^[a-f0-9]{40}$")
    expected_behavior: str = Field(min_length=1, max_length=8000)
    allowed_changes: tuple[str, ...] = Field(min_length=1, max_length=50)
    acceptance_checks: tuple[str, ...] = Field(min_length=1, max_length=20)
    approved_by: int = Field(gt=0)

    @field_validator("allowed_changes")
    @classmethod
    def source_scope(cls, paths: tuple[str, ...]) -> tuple[str, ...]:
        """Require explicit application files, not patterns or protected tests."""

        if len(set(paths)) != len(paths):
            raise ValueError("Duplicate scope paths")
        for path in paths:
            safe_path(path)
            if not path.startswith("src/"):
                raise ValueError("Approval scope must name application source")
        return paths


class CandidateRef(WireModel):
    run_id: str
    case_id: str
    attempt: int = Field(ge=1, le=3)
    plan_revision: int = Field(ge=1)
    base_sha: str = Field(pattern=r"^[a-f0-9]{40}$")
    digest: str = Field(pattern=r"^[a-f0-9]{64}$")


def files_digest(files: tuple[SourceFile, ...]) -> str:
    """Hash canonical paths and UTF-8 contents, independent of archive metadata."""

    if len({file.path for file in files}) != len(files):
        raise FixPolicyError("Duplicate candidate paths")
    payload = [
        (file.path, file.content) for file in sorted(files, key=lambda f: f.path)
    ]
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode()
    ).hexdigest()


class FrozenCandidate(WireModel):
    ref: CandidateRef
    files: tuple[SourceFile, ...]
    base_files: tuple[SourceFile, ...]

    @model_validator(mode="after")
    def valid_snapshot(self) -> FrozenCandidate:
        """Reject mutated snapshots and missing or preexisting regression tests."""

        if files_digest(self.files) != self.ref.digest:
            raise ValueError("Candidate digest mismatch")
        files_digest(self.base_files)
        if REGRESSION_PATH not in {file.path for file in self.files}:
            raise ValueError("Candidate requires one new regression test")
        if REGRESSION_PATH in {file.path for file in self.base_files}:
            raise ValueError("Regression test must be new on the captured base")
        return self


class FileChange(WireModel):
    path: str
    content: str = Field(max_length=65_536)

    @field_validator("path")
    @classmethod
    def writable_path(cls, path: str) -> str:
        """Limit writes to application source or the one reserved new test."""

        if path == REGRESSION_PATH:
            return path
        safe_path(path)
        if not path.startswith("src/"):
            raise ValueError("Existing tests are protected")
        return path


class FixResult(WireModel):
    status: Literal["candidate", "needs_reapproval", "blocked"]
    summary: str = Field(min_length=1, max_length=4000)


class AttemptReceipt(WireModel):
    run_id: str
    case_id: str
    attempt: int = Field(ge=1, le=3)
    plan_revision: int
    operation_id: str = Field(min_length=1, max_length=100)


class EvidenceRef(WireModel):
    artifact_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    candidate: CandidateRef
    kind: Literal["log", "png"]
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size: int = Field(gt=0)


class AssertionFailure(WireModel):
    title: str
    file: str
    line: int = Field(ge=1)
    message: str


class TestObservation(WireModel):
    title: str
    file: str
    status: Literal["passed", "failed", "timedOut", "skipped", "interrupted"]
    expected_status: str
    assertions: tuple[AssertionFailure, ...] = ()
    errors: tuple[str, ...] = ()


class TestReport(WireModel):
    tests: tuple[TestObservation, ...]
    errors: tuple[str, ...] = ()


class CheckEvidence(WireModel):
    candidate: CandidateRef
    kind: Literal["regression_on_base", "tests", "typecheck", "build"]
    status: Literal["passed", "failed", "blocked"]
    exit_code: int | None
    log: EvidenceRef
    report: TestReport | None = None
    assertion: AssertionFailure | None = None
    regression_sha256: str | None = None


class BrowserEvidence(WireModel):
    candidate: CandidateRef
    status: Literal["passed", "failed", "blocked"]
    checked_acceptance: tuple[str, ...]
    observation: str = Field(min_length=1, max_length=20_000)
    screenshot: EvidenceRef | None = None


class ReviewResult(WireModel):
    """Reviewer output has no authority to approve, increment, or publish."""

    candidate: CandidateRef
    blocking_findings: tuple[str, ...]
    scope_matches_plan: bool
    regression_is_behavioral: bool
    regression_not_weakened: bool
    summary: str = Field(min_length=1, max_length=8000)


class VerificationBundle(WireModel):
    candidate: CandidateRef
    regression_on_base: CheckEvidence
    tests_on_candidate: CheckEvidence
    typecheck: CheckEvidence
    build: CheckEvidence
    browser: BrowserEvidence
    review: ReviewResult


class VerificationResult(WireModel):
    status: Literal["passed", "failed", "blocked"]
    reason: str
    bundle: VerificationBundle | None = None
    checks: tuple[CheckEvidence, ...] = ()
    browser: BrowserEvidence | None = None
    review: ReviewResult | None = None


class RetryDecision(WireModel):
    action: Literal["retry", "reapprove", "handoff"]
    attempts_used: int = Field(ge=0, le=3)
    reason: str
