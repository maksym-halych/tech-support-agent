"""Trusted Phase 4 Docker operations; no model-defined executable or arguments."""

import logging
from typing import Literal

from pydantic import Field

from tech_support_agent.bug_resolution.adapters.commands import (
    CommandError,
    run_command,
)
from tech_support_agent.bug_resolution.adapters.sandbox import (
    DockerSandbox,
    SandboxError,
    SandboxPolicyError,
)
from tech_support_agent.bug_resolution.fix_contracts import FileChange
from tech_support_agent.bug_resolution.sandbox.protocol import (
    SourceFile,
    WireModel,
)


class WriteCommand(WireModel):
    op: Literal["write"] = "write"
    change: FileChange


class WriteReceipt(WireModel):
    written: str


class TestCommand(WireModel):
    op: Literal["tests"] = "tests"
    regression: bool


class TestExecution(WireModel):
    status: Literal["passed", "failed", "timeout"]
    exit_code: int | None
    output: str = Field(max_length=32_768)


logger = logging.getLogger(__name__)


async def write_file(sandbox: DockerSandbox, change: FileChange) -> WriteReceipt:
    """Write through the image-owned path guard after the caller checks its lease."""

    async with sandbox.lock:
        if sandbox.closed:
            raise SandboxError("Writer has been revoked")
        try:
            raw = await run_command(
                "docker",
                "exec",
                "--interactive",
                sandbox.name,
                "/usr/local/bin/node",
                "/opt/runner/fix-operations.mjs",
                input_data=WriteCommand(change=change).model_dump_json().encode(),
            )
            result = WriteReceipt.model_validate_json(raw)
            if result.written != change.path:
                raise SandboxError("Write receipt mismatch")
            return result
        except BaseException:
            await sandbox.close()
            raise


async def run_tests(sandbox: DockerSandbox, *, regression: bool) -> TestExecution:
    """Run fixed Playwright tests with an image-owned structured reporter."""

    async with sandbox.lock:
        if sandbox.closed:
            raise SandboxError("Sandbox is closed")
        try:
            raw = await run_command(
                "docker",
                "exec",
                "--interactive",
                sandbox.name,
                "/usr/local/bin/node",
                "/opt/runner/fix-operations.mjs",
                input_data=TestCommand(regression=regression)
                .model_dump_json()
                .encode(),
                timeout=70,
            )
            return TestExecution.model_validate_json(raw)
        except BaseException:
            await sandbox.close()
            raise


class SnapshotExport(WireModel):
    files: tuple[SourceFile, ...] = Field(min_length=1, max_length=100)


async def freeze_files(sandbox: DockerSandbox) -> tuple[SourceFile, ...]:
    """Stop all workspace processes, confirm quiescence, export, then destroy writers."""

    async with sandbox.lock:
        if sandbox.closed:
            raise SandboxError("Sandbox is closed")
        try:
            try:
                raw = await run_command(
                    "docker",
                    "exec",
                    sandbox.name,
                    "/usr/local/bin/node",
                    "/opt/runner/freeze.mjs",
                    max_output=2_000_000,
                )
            except CommandError as error:
                logger.error(
                    f"Private snapshot export error: {error.diagnostic.decode(errors='replace')}"
                )
                raise
            files = SnapshotExport.model_validate_json(raw).files
            if len({file.path for file in files}) != len(files):
                raise SandboxPolicyError("Duplicate snapshot paths")
            return tuple(sorted(files, key=lambda file: file.path))
        finally:
            # No success escapes unless all former writers have been destroyed.
            await sandbox.close()
