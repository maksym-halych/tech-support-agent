"""The tool set handed to the agent.

Kept out of ``tools/__init__.py`` on purpose, so importing a shared helper from
under ``tech_support_agent.tools`` does not pull in every tool module with it.
"""

from langchain_core.tools import BaseTool

from tech_support_agent.tools.get_current_datetime import get_current_datetime

TOOLS: list[BaseTool] = [
    get_current_datetime,
]

__all__ = [
    "TOOLS",
    "get_current_datetime",
]
