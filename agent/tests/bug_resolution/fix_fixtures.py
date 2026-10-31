"""Scripted Phase 4 dependencies; none of these answers exist in runtime profiles."""

from tech_support_agent.bug_resolution.fix_contracts import ApprovedFixPlan, FileChange
from tech_support_agent.bug_resolution.fixing import FixSession
from tech_support_agent.bug_resolution.sandbox.protocol import (
    CheckResult,
    FileContent,
    Operation,
    ReadFile,
    Result,
    SourceFile,
)

BASE_SHA = "a" * 40
BASE_FILES = (
    SourceFile(path="src/grade.ts", content="broken"),
    SourceFile(path="tests/smoke.spec.ts", content="protected"),
)


def plan(revision: int = 1, **changes: object) -> ApprovedFixPlan:
    data = {
        "run_id": "run",
        "case_id": "case",
        "revision": revision,
        "base_sha": BASE_SHA,
        "expected_behavior": "Continue enables and reaches goal",
        "allowed_changes": ("src/grade.ts",),
        "acceptance_checks": ("Continue is enabled", "Goal screen appears"),
        "approved_by": 123,
    }
    data.update(changes)
    return ApprovedFixPlan.model_validate(data)


class MemoryEditor:
    def __init__(self) -> None:
        self.initial_files = BASE_FILES
        self.files = {file.path: file for file in self.initial_files}
        self.closed = False
        self.writes = 0

    async def write(self, change: FileChange) -> None:
        assert not self.closed
        self.writes += 1
        self.files[change.path] = SourceFile(path=change.path, content=change.content)

    async def request(self, command: Operation, *, timeout: float = 75) -> Result:
        if isinstance(command, ReadFile):
            file = self.files[command.path]
            return FileContent(path=file.path, content=file.content)
        return CheckResult(
            check=command.kind, status="passed", exit_code=0, output="probe"
        )

    async def freeze(self) -> tuple[SourceFile, ...]:
        self.closed = True
        return tuple(self.files.values())

    async def close(self) -> None:
        self.closed = True


def session(**kwargs: object) -> tuple[FixSession, list[MemoryEditor]]:
    editors = []

    async def factory() -> MemoryEditor:
        editor = MemoryEditor()
        editors.append(editor)
        return editor

    return FixSession("run", "case", BASE_SHA, factory, **kwargs), editors


async def edit_and_freeze(subject: FixSession, operation: str = "first"):
    tools = await subject.begin_attempt(operation, subject.approval.revision)
    await tools.apply_patch(FileChange(path="src/grade.ts", content="fixed"))
    await tools.apply_patch(
        FileChange(path="tests/regression.spec.ts", content="behavior test")
    )
    return await subject.freeze(tools)
