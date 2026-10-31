"""Read-only source tools; no edit, shell, dependency, or workflow capability."""

from langchain_core.tools import BaseTool, tool

from tech_support_agent.bug_resolution.adapters.sandbox import DockerSandbox
from tech_support_agent.bug_resolution.sandbox.protocol import ReadFile, Search


def file_tools(sandbox: DockerSandbox) -> list[BaseTool]:
    """Bind source inspection to a single Docker workspace."""

    @tool
    async def read_file(path: str) -> str:
        """Read a bounded regular source file within the read-only allowlist."""

        return (await sandbox.request(ReadFile(path=path))).model_dump_json()

    @tool
    async def search_code(query: str) -> str:
        """Search source for literal text; query is not a regex or shell argument."""

        return (await sandbox.request(Search(query=query))).model_dump_json()

    return [read_file, search_code]
