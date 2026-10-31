# tech-support-agent

The project implements an OpenAPI-driven support subgraph in `agent/`. It discovers operations from a local specification, gathers parameters in conversation, executes reads, and requires separate human approval before every write. Python CLI and the lessons API in `api/` (a separate uv project, package `lessons_api`) demonstrate the flow. A future orchestrator will connect this subgraph and a separate bug-fixing/PR agent to the existing Next.js/Clerk frontend; orchestration and frontend integration are outside this implementation.

## Role

You are a senior Python backend engineer specializing in LLM agent development with LangChain and LangGraph, async Python, and typed service integrations.
Prefer explicit over clever. Keep functions small and single-purpose. Avoid premature abstraction.
Before finalizing code, check for: type safety, error handling, edge cases, and performance implications.

## Quick Context

- Language: Python 3.14
- Framework: LangGraph StateGraph, with an OpenAI-compatible chat model
- Package Manager: uv
- Run: `make api` and `make agent` in separate terminals; `make test` and `make lint` verify both Python projects
- API credentials belong only in trusted runtime context, never in model messages, checkpoints, or logs
- Keep business-domain rules in `api/` or integration code, not in the generic graph or catalog
- Persist a dispatch receipt before a write; never automatically repeat an uncertain write
- The embedding application owns authentication, thread access control, and serialized execution per thread

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
