# tech-support-agent

A lessons platform in three apps: `api/` is the lessons API (FastAPI, SQLite, package `lessons_api`) where teachers open lesson slots and students book and cancel them; `agent/` is a lesson assistant (LangChain `create_agent`, package `tech_support_agent`) served by LangGraph Agent Server; `frontend/` is the Next.js/Clerk app: the lessons dashboard, where teachers open and cancel lessons and students book and cancel them, plus a right-side chat panel that talks to Agent Server through the LangGraph SDK and shows an approval card for every write. The refactor that produced this layout is documented task by task in `docs/`; keep its notes in sync when you change these areas.

## Role

You are a senior Python backend engineer specializing in LLM agent development with LangChain and LangGraph, async Python, and typed service integrations.
Prefer explicit over clever. Keep functions small and single-purpose. Avoid premature abstraction.
Before finalizing code, check for: type safety, error handling, edge cases, and performance implications.

## Quick Context

- Language: Python 3.14 (`api/`, `agent/`), TypeScript/Next.js (`frontend/`)
- Frameworks: FastAPI; LangChain `create_agent` with middleware, served by Agent Server (`langgraph dev`); an OpenAI-compatible chat model
- Package managers: uv for both Python projects, pnpm for the frontend
- Run: `make api`, `make agent` and `make frontend` in separate terminals; `make test` and `make lint` verify both Python projects
- Identity is the Clerk session JWT only. The API and Agent Server both verify it; the role comes from the `role` claim and only `teacher` makes a teacher
- The agent acts as the signed-in user; the user's token must never reach model messages, graph state, checkpoints or logs
- Lesson rules (who may book, cancel or create what) live in `api/`; the agent only proposes actions and the API enforces them
- Every lesson write goes through `HumanInTheLoopMiddleware`; never add a write tool without adding it to `WRITE_TOOLS` and the approval middleware
- Frontend checks: `pnpm -C frontend lint`, `pnpm -C frontend format:check`, `pnpm -C frontend build`
- Feature docs live in `docs/features/<feature>/` (10 files each, ASD-STE100 English). Read the feature's docs before you change it. After a code change, check the touched features through their `entry-points.json`; if the docs no longer match the code, ask "Update the feature docs?" and, on yes, update them in the same commit. See `docs/features/README.md`

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
