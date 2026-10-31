"""Case-local approval ledger, global attempts, and revocable editing capabilities."""

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Protocol

from tech_support_agent.bug_resolution.adapters.fix_sandbox import (
    freeze_files,
    write_file,
)
from tech_support_agent.bug_resolution.adapters.sandbox import (
    DemoProfile,
    DockerSandbox,
)
from tech_support_agent.bug_resolution.fix_contracts import (
    REGRESSION_PATH,
    ApprovedFixPlan,
    AttemptReceipt,
    CandidateRef,
    FileChange,
    FixPolicyError,
    FrozenCandidate,
    RetryDecision,
    VerificationBundle,
    files_digest,
)
from tech_support_agent.bug_resolution.sandbox.protocol import (
    CheckResult,
    Click,
    Empty,
    FileContent,
    Navigate,
    Operation,
    ReadFile,
    Result,
    RunCheck,
    Search,
    Select,
    SourceFile,
)


class EditableSandbox(Protocol):
    """Injectable infrastructure boundary; only trusted factories receive this."""

    initial_files: tuple[SourceFile, ...]

    async def request(self, command: Operation, *, timeout: float = 75) -> Result: ...
    async def close(self) -> None: ...
    async def write(self, change: FileChange) -> None: ...
    async def freeze(self) -> tuple[SourceFile, ...]: ...


class DockerEditor:
    """Adapt the read-only Phase 2 channel to explicitly authorized edits."""

    def __init__(self, sandbox: DockerSandbox) -> None:
        self._sandbox = sandbox
        self.initial_files = sandbox.initial_files

    async def request(self, command: Operation, *, timeout: float = 75) -> Result:
        """Delegate a bounded read/browser/probe operation."""

        return await self._sandbox.request(command, timeout=timeout)

    async def write(self, change: FileChange) -> None:
        """Execute one guarded replacement inside Docker."""

        await write_file(self._sandbox, change)

    async def freeze(self) -> tuple[SourceFile, ...]:
        """Export only after process-wide quiescence, destroying the original."""

        return await freeze_files(self._sandbox)

    async def close(self) -> None:
        """Destroy all processes and writable storage belonging to this attempt."""

        await self._sandbox.close()


def docker_editor_factory(
    profile: DemoProfile,
) -> Callable[[], Awaitable[EditableSandbox]]:
    """Bind an operator-captured base; arguments never come from a model."""

    async def create() -> EditableSandbox:
        return DockerEditor(await DockerSandbox.prepare(profile))

    return create


def validate_scope(candidate: FrozenCandidate, plan: ApprovedFixPlan) -> None:
    """Check the complete snapshot, not just paths mentioned in tool receipts."""

    ref = candidate.ref
    if (ref.run_id, ref.case_id, ref.plan_revision, ref.base_sha) != (
        plan.run_id,
        plan.case_id,
        plan.revision,
        plan.base_sha,
    ) or files_digest(candidate.files) != ref.digest:
        raise FixPolicyError("Candidate approval or digest mismatch")
    base = {file.path: file.content for file in candidate.base_files}
    current = {file.path: file.content for file in candidate.files}
    if base.keys() - current.keys():
        raise FixPolicyError("Candidate deleted a base file")
    changed = {path for path in current if current[path] != base.get(path)}
    if not changed <= {*plan.allowed_changes, REGRESSION_PATH}:
        raise FixPolicyError("Candidate changed a protected or unapproved file")
    if REGRESSION_PATH not in changed or not changed.intersection(plan.allowed_changes):
        raise FixPolicyError(
            "Candidate needs source changes and one new regression test"
        )


class FixSession:
    """Trusted per-case ledger. Keep one instance through local and CI retries.

    Approval installation must be called only after Phase 3 authenticates and
    consumes the Telegram decision. Neither this object nor its approval methods
    belongs in any model tool registry or checkpoint payload.
    """

    def __init__(
        self,
        run_id: str,
        case_id: str,
        base_sha: str,
        factory: Callable[[], Awaitable[EditableSandbox]],
        *,
        clock: Callable[[], float] = time.monotonic,
        max_tools: int = 30,
        max_seconds: float = 300,
    ) -> None:
        if max_tools < 1 or max_seconds <= 0:
            raise ValueError("Positive fix budgets required")
        self.run_id, self.case_id, self.base_sha = run_id, case_id, base_sha
        self._factory, self._clock = factory, clock
        self.max_tools, self.max_seconds = max_tools, max_seconds
        self._attempts = 0
        self._revision = 0
        self._approval: ApprovedFixPlan | None = None
        self._sandbox: EditableSandbox | None = None
        self._lease: ScopedFixTools | None = None
        self._operations: set[str] = set()
        self._candidate: FrozenCandidate | None = None
        self._verification: VerificationBundle | None = None
        self._terminal = False
        self._lock = asyncio.Lock()

    @property
    def attempts_used(self) -> int:
        return self._attempts

    @property
    def approval(self) -> ApprovedFixPlan | None:
        return self._approval

    @property
    def candidate(self) -> FrozenCandidate | None:
        return self._candidate

    @property
    def verification(self) -> VerificationBundle | None:
        return self._verification

    async def install_approval(self, plan: ApprovedFixPlan) -> None:
        """Install a trusted, newly consumed approval without resetting attempts."""

        plan = ApprovedFixPlan.model_validate(plan)
        async with self._lock:
            if (plan.run_id, plan.case_id, plan.base_sha) != (
                self.run_id,
                self.case_id,
                self.base_sha,
            ):
                raise FixPolicyError("Wrong run, case, or base")
            if self._terminal or self._lease is not None:
                raise FixPolicyError("Cannot approve an active or terminal attempt")
            if self._approval == plan:
                return
            if plan.revision <= self._revision:
                raise FixPolicyError("Approval revision must advance")
            self._approval, self._revision = plan, plan.revision
            self._candidate = self._verification = None

    async def begin_attempt(self, operation_id: str, revision: int) -> ScopedFixTools:
        """Increment once before creating writes; replay never starts another attempt."""

        async with self._lock:
            if (
                not self._approval
                or self._approval.revision != revision
                or self._terminal
            ):
                raise FixPolicyError("Current explicit approval required")
            if operation_id in self._operations:
                if self._lease and self._lease.receipt.operation_id == operation_id:
                    return self._lease
                raise FixPolicyError("Attempt operation already consumed")
            if self._lease is not None or self._attempts >= 3:
                raise FixPolicyError("Active attempt or global attempt limit")
            receipt = AttemptReceipt(
                run_id=self.run_id,
                case_id=self.case_id,
                attempt=self._attempts + 1,
                plan_revision=revision,
                operation_id=operation_id,
            )
            self._operations.add(operation_id)
            self._attempts += 1
            self._candidate = self._verification = None
            try:
                self._sandbox = await self._factory()
                if REGRESSION_PATH in {
                    file.path for file in self._sandbox.initial_files
                }:
                    raise FixPolicyError(
                        "Base already contains reserved regression test"
                    )
                self._lease = ScopedFixTools(self, receipt, self._clock())
                return self._lease
            except BaseException:
                if self._sandbox:
                    await self._sandbox.close()
                    self._sandbox = None
                raise

    def _authorize(
        self, lease: ScopedFixTools, *, count_tool: bool = True
    ) -> EditableSandbox:
        if self._lease is not lease or not self._sandbox or not self._approval:
            raise FixPolicyError("Write capability revoked")
        if lease.receipt.plan_revision != self._approval.revision:
            raise FixPolicyError("Stale approval revision")
        if self._clock() - lease.started >= self.max_seconds or (
            count_tool and lease.calls >= self.max_tools
        ):
            raise FixPolicyError("Fix stage budget exhausted")
        if count_tool:
            lease.calls += 1
        return self._sandbox

    async def freeze(self, lease: ScopedFixTools) -> FrozenCandidate:
        """Revoke the lease before asking Docker to freeze and destroy every writer."""

        async with self._lock:
            sandbox = self._authorize(lease, count_tool=False)
            self._lease = None
            try:
                files = await sandbox.freeze()
                ref = CandidateRef(
                    run_id=self.run_id,
                    case_id=self.case_id,
                    attempt=self._attempts,
                    plan_revision=self._revision,
                    base_sha=self.base_sha,
                    digest=files_digest(files),
                )
                candidate = FrozenCandidate(
                    ref=ref, files=files, base_files=sandbox.initial_files
                )
                assert self._approval is not None
                validate_scope(candidate, self._approval)
                self._candidate = candidate
                return candidate
            finally:
                await sandbox.close()
                self._sandbox = None

    def require_current(self, candidate: FrozenCandidate) -> ApprovedFixPlan:
        """Recheck freshness before and after verification or future publication."""

        if self._terminal or not self._approval or self._candidate != candidate:
            raise FixPolicyError("Candidate is not current")
        validate_scope(candidate, self._approval)
        return self._approval

    async def record_verification(
        self, candidate: FrozenCandidate, bundle: VerificationBundle
    ) -> None:
        """Store only the current result after the trusted verifier checks all gates."""

        from tech_support_agent.bug_resolution.verification import validate_gates

        async with self._lock:
            validate_gates(bundle, candidate, self.require_current(candidate))
            self._verification = bundle

    async def retry_or_handoff(
        self, reason: str, *, new_approach: bool = False
    ) -> RetryDecision:
        """Invalidate all evidence; reapproval and CI failures share this same ledger."""

        async with self._lock:
            self._lease = None
            self._candidate = self._verification = None
            if self._sandbox:
                await self._sandbox.close()
                self._sandbox = None
            if new_approach:
                self._approval = None
            if self._attempts >= 3 or self._terminal:
                self._terminal = True
                self._approval = None
                action = "handoff"
            else:
                action = "reapprove" if self._approval is None else "retry"
            return RetryDecision(
                action=action, attempts_used=self._attempts, reason=reason
            )

    async def close(self) -> None:
        """Revoke writes and destroy resources on worker cancellation or termination."""

        async with self._lock:
            self._terminal = True
            self._lease = None
            self._approval = None
            self._candidate = self._verification = None
            if self._sandbox:
                await self._sandbox.close()
                self._sandbox = None


class ScopedFixTools:
    """The only capability a fixer receives; probes never create final evidence."""

    def __init__(
        self, session: FixSession, receipt: AttemptReceipt, started: float
    ) -> None:
        self._session = session
        self.receipt, self.started, self.calls = receipt, started, 0

    async def apply_patch(self, change: FileChange) -> None:
        """Replace one allowlisted file; additions are restricted to approved source/test."""

        change = FileChange.model_validate(change)
        session = self._session
        async with session._lock:
            sandbox = session._authorize(self)
            assert session.approval is not None
            if change.path not in (*session.approval.allowed_changes, REGRESSION_PATH):
                raise FixPolicyError("Path is outside approved scope")
            await sandbox.write(change)

    async def inspect(self, command: Operation) -> Result:
        """Expose read/browser operations only, charging the same hard tool budget."""

        if not isinstance(command, (ReadFile, Search, Empty, Navigate, Click, Select)):
            raise FixPolicyError("Operation is not a fixer read/browser tool")
        session = self._session
        async with session._lock:
            return await session._authorize(self).request(command)

    async def read_file(self, path: str) -> FileContent:
        """Read a guarded source/test file without granting a raw sandbox handle."""

        result = await self.inspect(ReadFile(path=path))
        assert isinstance(result, FileContent)
        return result

    async def run_check(self, command: RunCheck) -> CheckResult:
        """Return editing feedback only; final verification always runs after freeze."""

        command = RunCheck.model_validate(command)
        session = self._session
        async with session._lock:
            result = await session._authorize(self).request(command)
            assert isinstance(result, CheckResult)
            return result
