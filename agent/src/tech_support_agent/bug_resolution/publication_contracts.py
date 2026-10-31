"""Private, narrow integration contracts; none of these are model-facing tools.

The Phase 4 bridge must export the complete frozen src/tests manifest and map real
verification results into LocalGate records. The coordinator supplies authority
from its current locked case, never from an LLM or a public request.
"""

import hashlib
import re
from pathlib import PurePosixPath
from typing import Annotated, Literal, Protocol, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)

Sha = Annotated[str, Field(pattern=r"^[a-f0-9]{40}$")]
Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
CaseId = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")]
Repo = Annotated[
    str,
    Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$"),
]
Positive = Annotated[int, Field(gt=0, strict=True)]
Attempt = Annotated[int, Field(ge=1, le=3, strict=True)]
Text = Annotated[str, Field(min_length=1, max_length=8000)]


class PublicationError(RuntimeError):
    """Safe failure requiring operator attention, not another fix attempt."""


class PublicationPending(PublicationError):
    """Uncertain transport outcome; reconcile before repeating any side effect."""


class Record(BaseModel):
    """Immutable, revalidated service boundary with no implicit success defaults."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        revalidate_instances="always",
        hide_input_in_errors=True,
    )


def source_path(value: str) -> str:
    """Accept canonical application files and behavior-level test files only."""

    parts = value.split("/")
    if (
        len(value) > 200
        or not re.fullmatch(r"[A-Za-z0-9_/-]+(?:\.[A-Za-z0-9_-]+)+", value)
        or any(
            p in ("", ".", "..", "node_modules", "dist") or p.startswith(".")
            for p in parts
        )
        or not (
            value.startswith("src/")
            and PurePosixPath(value).suffix in (".ts", ".tsx", ".css")
            or value.startswith("tests/")
            and value.endswith(".spec.ts")
        )
    ):
        raise ValueError("Export path is outside the source/test allowlist")
    return value


class ExportFile(Record):
    """Regular-file bytes from a quiescent snapshot; no host paths or file modes."""

    path: str
    content: bytes = Field(max_length=65536, repr=False)
    validate_path = field_validator("path")(source_path)


def manifest_digest(files: tuple[ExportFile, ...]) -> str:
    """Hash sorted paths and exact bytes with length framing, without Git filters.

    Each UTF-8 path and content is prefixed by its unsigned 8-byte big-endian
    length. Phase 4 must use this digest or explicitly attest this export digest
    alongside its own candidate digest at the trusted integration boundary.
    """

    digest = hashlib.sha256()
    paths = [file.path for file in files]
    if not files or len(files) > 100 or len(set(paths)) != len(paths):
        raise ValueError("Invalid or duplicate export manifest")
    if sum(len(file.content) for file in files) > 2_000_000:
        raise ValueError("Export exceeds byte budget")
    for file in sorted(files, key=lambda file: file.path):
        for part in (file.path.encode("utf-8"), file.content):
            digest.update(len(part).to_bytes(8, "big"))
            digest.update(part)
    return digest.hexdigest()


class CandidateIdentity(Record):
    """Full provenance required on every verification record and CI event."""

    case_id: CaseId
    attempt: Attempt
    plan_revision: Positive
    base_sha: Sha
    digest: Digest
    snapshot_digest: Digest | None = None


class PublicationAuthority(Record):
    """Current coordinator-owned approval, not a model-produced assertion."""

    run_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    case_id: CaseId
    attempt_count: Attempt
    plan_revision: Positive
    approved_revision: Positive | None
    approved_by: Positive | None
    investigation: Literal["confirmed", "unconfirmed", "blocked"]
    phase: Literal["verified", "waiting_ci", "rejected", "needs_approval"]


class LocalGate(Record):
    """A real result mapped by the verifier, with candidate-bound evidence."""

    candidate: CandidateIdentity
    kind: Literal["regression_on_base", "tests", "build", "browser", "review"]
    outcome: Literal["passed", "expected_assertion_failure", "failed", "blocked"]
    evidence_id: str = Field(min_length=1, max_length=128)
    summary: Text


class PublicationEvidence(Record):
    """PNG bytes and factual report fields; publisher chooses all output names."""

    candidate: CandidateIdentity
    before_png: bytes = Field(min_length=1, max_length=2_000_000, repr=False)
    after_png: bytes = Field(min_length=1, max_length=2_000_000, repr=False)
    problem: Text
    cause: Text
    approved_behavior: Text
    changes: Text


class VerifiedExport(Record):
    """Complete immutable src/tests snapshot and independently obtained gates."""

    candidate: CandidateIdentity
    files: tuple[ExportFile, ...] = Field(min_length=1, max_length=100, strict=False)
    regression_path: str
    gates: tuple[LocalGate, ...] = Field(strict=False)
    evidence: PublicationEvidence
    validate_path = field_validator("regression_path")(source_path)


class PublisherConfig(Record):
    """Operator-selected repository/workflow and explicit live-write permission."""

    repo: Repo
    base_branch: Literal["demo/continue", "demo/both"]
    workflow_id: Positive
    workflow_path: Literal[".github/workflows/verify.yml"] = (
        ".github/workflows/verify.yml"
    )
    required_jobs: tuple[Literal["tests", "build"], ...] = ("tests", "build")
    live_authorized: bool = False

    @model_validator(mode="after")
    def both_jobs(self) -> Self:
        """Do not permit configuration to weaken the trusted CI gate."""

        if (
            set(self.required_jobs) != {"tests", "build"}
            or len(self.required_jobs) != 2
        ):
            raise ValueError("Both tests and build are required")
        return self


class GitHubCredentials(Record):
    """Host-only token injected explicitly, never serialized into graph state."""

    token: SecretStr


class PublicationRef(Record):
    """Checkpoint-safe publication identity; attempt budget remains external."""

    run_id: str
    candidate: CandidateIdentity
    repo: Repo
    base_branch: Literal["demo/continue", "demo/both"]
    branch: str
    pr_number: Positive
    head_sha: Sha
    workflow_id: Positive
    workflow_path: str
    required_jobs: tuple[str, ...] = Field(strict=False)
    pr_url: str

    @model_validator(mode="after")
    def fixed_coordinates(self) -> Self:
        """Reject a forged URL or branch even when restoring a checkpoint."""

        if (
            self.branch != f"fix/{self.candidate.case_id}"
            or self.pr_url != f"https://github.com/{self.repo}/pull/{self.pr_number}"
        ):
            raise ValueError("Publication coordinates do not match case/repository")
        if (
            self.workflow_path != ".github/workflows/verify.yml"
            or set(self.required_jobs) != {"tests", "build"}
            or len(self.required_jobs) != 2
        ):
            raise ValueError("Unexpected CI contract")
        return self


class RemoteHeads(Record):
    base_sha: Sha
    branch_sha: Sha | None


class PullRequest(Record):
    number: Positive
    node_id: str
    repo: Repo
    head_repo: Repo
    base_branch: str
    base_sha: Sha
    branch: str
    head_sha: Sha
    merge_sha: Sha | None
    state: Literal["open", "closed"]
    draft: bool
    body: str


class WorkflowIdentity(Record):
    id: Positive
    path: str
    state: str


class RunPullRequest(Record):
    number: Positive
    head_sha: Sha
    base_sha: Sha


class WorkflowRun(Record):
    id: Positive
    workflow_id: Positive
    path: str
    repo: Repo
    head_repo: Repo
    event: str
    head_sha: Sha
    head_branch: str
    run_number: Positive
    run_attempt: Positive
    status: str
    conclusion: str | None
    pull_requests: tuple[RunPullRequest, ...]


class WorkflowJob(Record):
    id: Positive
    run_id: Positive
    run_attempt: Positive
    head_sha: Sha
    name: str
    status: str
    conclusion: str | None


class CIObservation(Record):
    """A poll result tied to one wait; pending never spends a fix attempt."""

    publication: PublicationRef
    status: Literal["pending", "passed", "failed", "blocked"]
    reason: str
    run_id: Positive | None = None
    run_attempt: Positive | None = None


class CIWait(Record):
    """Coordinator must correlate the whole identity before private resume."""

    publication: PublicationRef


class GitHubPort(Protocol):
    """Bounded GitHub operations; implementation owns argv and authentication."""

    async def heads(self, branch: str) -> RemoteHeads: ...
    async def find_pr(self, branch: str) -> tuple[PullRequest, ...]: ...
    async def get_pr(self, number: int) -> PullRequest: ...
    async def create_pr(self, branch: str, title: str, body: str) -> None: ...
    async def update_body(self, number: int, body: str) -> None: ...
    async def set_ready(self, number: int, *, ready: bool) -> None: ...
    async def workflow(self) -> WorkflowIdentity: ...
    async def runs(self) -> tuple[WorkflowRun, ...]: ...
    async def jobs(self, run: WorkflowRun) -> tuple[WorkflowJob, ...]: ...
    async def commit_parents(self, sha: str) -> tuple[str, ...]: ...


class PreparedCommit(Record):
    """Typed local Git result, separate from remote publication success."""

    head_sha: Sha
    parent_sha: Sha
    candidate: CandidateIdentity


class EvidenceFile(Record):
    """Generated evidence bytes; never an arbitrary host path."""

    path: str = Field(
        pattern=r"^docs/evidence/[a-z0-9][a-z0-9-]{0,79}/(?:attempt-[1-3]-(?:before|after)\.png|report\.md)$"
    )
    content: bytes = Field(max_length=2_000_000, repr=False)


class EvidenceExport(Record):
    """Exactly the two validated images and current report for one attempt."""

    files: tuple[EvidenceFile, ...] = Field(min_length=3, max_length=3)


class GitPort(Protocol):
    """No shell or project commands; files enter Git as literal blob bytes."""

    async def prepare(
        self,
        export: VerifiedExport,
        previous: PublicationRef | None,
        evidence: EvidenceExport,
    ) -> PreparedCommit: ...
    async def verify(self, commit: PreparedCommit, export: VerifiedExport) -> None: ...
    async def push(self, commit: PreparedCommit, branch: str) -> None: ...
