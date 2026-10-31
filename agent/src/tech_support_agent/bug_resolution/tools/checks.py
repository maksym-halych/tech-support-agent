"""Fixer-only tools bound to an expiring attempt capability, never a raw sandbox."""

from typing import Literal

from langchain_core.tools import BaseTool, tool

from tech_support_agent.bug_resolution.fix_contracts import FileChange
from tech_support_agent.bug_resolution.fixing import ScopedFixTools
from tech_support_agent.bug_resolution.sandbox.protocol import RunCheck


def fix_tools(capability: ScopedFixTools) -> list[BaseTool]:
    """Bind write/probe tools; probe output cannot populate final verification."""

    @tool
    async def apply_patch(path: str, content: str) -> str:
        """Replace an approved source file or the one new tests/regression.spec.ts."""

        await capability.apply_patch(FileChange(path=path, content=content))
        return "File replaced in the editable attempt; not yet verified."

    @tool
    async def run_check(kind: Literal["typecheck", "tests", "build"]) -> str:
        """Run a fixed editing probe; final checks run again on the frozen candidate."""

        return (await capability.run_check(RunCheck(kind=kind))).model_dump_json()

    return [apply_patch, run_check]
