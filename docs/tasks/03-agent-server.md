# Task 3 — Back to Agent Server with Clerk auth

## Goal

Serve the agent from Agent Server again (`langgraph dev` on port 2024), as a single `create_agent` graph, with Clerk JWT authentication and per-user thread isolation. Remove the generic OpenAPI stack from this branch.

## Remove

- `catalog.py`, `api.py` (HTTP executor), `journal.py`, `cli.py`, the OpenAPI-specific models in `models.py`, `examples/`, and their tests (`test_api.py`, `test_catalog.py`, `test_journal.py`, `smoke_demo.py`, generic parts of `test_graph.py`).
- Dependencies no longer needed: `openapi-spec-validator`, `openapi-schema-validator`, `pyyaml`, `langgraph-checkpoint-sqlite` (Agent Server persists threads), the editable `../api` dev dependency added in task 1.
- `SUPPORT_*` settings and the CLI script.

## Restore and add

1. `agent/langgraph.json`: graph `agent` → `tech_support_agent.graph:build_agent`, `env: .env`, `python_version: "3.14"`, and `auth: {"path": "./src/tech_support_agent/auth.py:auth"}`.
2. Dev dependency `langgraph-cli[inmem]`; Makefile `agent` target back to `uv run --directory agent langgraph dev --no-browser`; `.claude/launch.json` entry `agent` on port 2024.
3. `graph.py` in the shape it had on `main`: `create_chat_model(settings)` using the Responses API for OpenAI's own endpoint (as in commit `f142373`), `create_lessons_agent(model)` and a no-argument `build_agent()` factory.
4. Middleware, restored from `main`: tool call limit per run. New: a `@dynamic_prompt` that adds the caller's name, role, timezone and current local date to the system prompt.
5. `auth.py` (LangGraph custom auth):
   - `@auth.authenticate` reads `Authorization: Bearer`, verifies the Clerk JWT exactly as the API does (issuer, JWKS, `exp`, `azp`), and returns a `MinimalUserDict` with `identity` = `sub`, `display_name`, `role` and the raw token for task 4's API client. Invalid or missing token → 401.
   - `@auth.on` handlers: on thread/run create, stamp `metadata.owner = identity`; on read/search/update/delete, filter by `owner`. Assistants are read-only for users.
6. Run context: `context_schema` with `timezone` (IANA, validated, default `Europe/Kyiv`) supplied by the frontend on each run.
7. `prompts.py`: lesson assistant prompt — helps students find and book lessons and cancel bookings, helps teachers open and cancel lessons, always confirms exact local date/time before proposing a write, never claims a write happened unless a tool result says so.
8. `config.py`: `CLERK_ISSUER`, `CLERK_AUTHORIZED_PARTIES`, `LESSONS_API_URL` (used in task 4). Update `agent/.env.example`.
9. Frontend: `NEXT_PUBLIC_API_URL=http://localhost:2024` and `NEXT_PUBLIC_ASSISTANT_ID=agent` work again on `/chat`.

## Things to verify while implementing

- `langgraph dev` runs on Python 3.14 with custom auth enabled.
- Whether the user dict returned by `@auth.authenticate` (exposed as `config["configurable"]["langgraph_auth_user"]`) is persisted into checkpoint or run metadata. If the raw token would be stored, keep it out of the user dict and pass it to tools another way (e.g. a request-scoped context variable set in the auth handler), and record the choice here.
- Clerk tokens live about 60 seconds. Approval resumes are new requests with fresh tokens, so they are fine; confirm a normal multi-tool run finishes inside the token's lifetime, and if not, document the limit.

## Tests

- Auth: valid token → identity and role; expired, wrong issuer, wrong `azp`, missing header → 401; role defaults to student.
- Thread isolation handlers: owner stamped on create; filter applied on read.
- Graph: builds with a fake chat model; dynamic prompt contains role and timezone; tool call limit still enforced.

## Acceptance criteria

- `make agent` starts Agent Server on :2024; `/info` responds; unauthenticated thread calls get 401.
- Signed in on the frontend, `/chat` talks to the agent; another user cannot open the first user's thread.
- `pytest`, `ruff check`, `ruff format --check` pass. No file under `agent/src` references the removed modules.
