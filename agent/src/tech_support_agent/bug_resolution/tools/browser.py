"""Read-only browser tool factory, without JavaScript or arbitrary URLs."""

from pathlib import Path

from langchain_core.tools import BaseTool, tool

from tech_support_agent.bug_resolution.adapters.sandbox import DockerSandbox
from tech_support_agent.bug_resolution.sandbox.protocol import (
    Click,
    Empty,
    Navigate,
    Select,
)


def browser_tools(sandbox: DockerSandbox, artifacts: Path) -> list[BaseTool]:
    """Bind investigation tools to one owned container and artifact directory."""

    @tool
    async def inspect_page() -> str:
        """Inspect the demo page and obtain fresh short-lived element references."""

        return (await sandbox.request(Empty(op="inspect"))).model_dump_json()

    @tool
    async def navigate(path: str) -> str:
        """Navigate to a plain demo-relative path, such as /; no foreign URLs."""

        return (await sandbox.request(Navigate(path=path))).model_dump_json()

    @tool
    async def click(element_ref: str) -> str:
        """Click a reference from the most recent snapshot; inspect again if stale."""

        return (await sandbox.request(Click(ref=element_ref))).model_dump_json()

    @tool
    async def select(element_ref: str, value: str) -> str:
        """Select an option using a current snapshot reference and literal value."""

        return (
            await sandbox.request(Select(ref=element_ref, value=value))
        ).model_dump_json()

    @tool
    async def screenshot() -> str:
        """Capture a real PNG; return its generated, validated artifact reference."""

        return (await sandbox.screenshot(artifacts)).model_dump_json()

    return [inspect_page, navigate, click, select, screenshot]
