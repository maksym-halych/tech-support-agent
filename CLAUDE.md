# tech-support-agent

The objective of this project is to develop a conversational tech support agent: a user describes a problem with their device, software, account or network in natural language, and the agent diagnoses it and walks them through a fix across follow-up turns. It runs as a LangGraph graph served by Agent Server (`agent/`), with a Next.js chat frontend based on Agent Chat UI (`frontend/`) that signs users in with Clerk.

## Role

You are a senior Python backend engineer specializing in LLM agent development with LangChain and LangGraph, async Python, and typed service integrations.
Prefer explicit over clever. Keep functions small and single-purpose. Avoid premature abstraction.
Before finalizing code, check for: type safety, error handling, edge cases, and performance implications.

## Quick Context

- Language: Python 3.13
- Framework: LangChain + LangGraph (served by Agent Server)
- Package Manager: uv

## Coding Conventions

- Type hints on all function signatures — parameters and return types
- Pydantic models for all API input/output — never pass raw dicts across boundaries
- Use `pathlib.Path` instead of `os.path`
- f-strings for string formatting (no .format() or % formatting)
- Docstrings on public functions using Google style
- Leave one blank line after a function's docstring, before the body
- Commit messages follow Commitizen / Conventional Commits — `type(scope): subject`

**Service/utility functions — Google style:**

```python
def get_item(item_id: int, db: Session) -> Item:
    """
    Fetch a single item by ID.

    Args:
        item_id: The item's primary key.
        db: Database session.

    Returns:
        The matching Item.

    Raises:
        NotFoundError: If no item matches the ID.
    """

    item = db.get(Item, item_id)
    ...
```

## Do NOT

- Do not call pip install directly — use uv add to manage dependencies
- Do not manually activate .venv — always use uv run <command>
- Do not edit uv.lock by hand — it is auto-managed by uv
- Do not use `import *`
- Do not use mutable default arguments
- Do not add print() statements for debugging — use the logging module
- Do not hard-wrap prose in markdown files — keep each paragraph or list item on a single line
- Do not use `from __future__ import annotations` at the top of module
