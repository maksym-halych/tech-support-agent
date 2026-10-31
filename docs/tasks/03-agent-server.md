# Task 3 — Back to Agent Server with Clerk auth

## Goal

Serve the agent from Agent Server again (`langgraph dev` on port 2024), as a single `create_agent` graph, with Clerk JWT authentication and per-user thread isolation. Remove the generic OpenAPI stack from this branch.

## Remove

- `catalog.py`, `api.py` (HTTP executor), `journal.py`, `cli.py`, the OpenAPI-specific models in `models.py`, `examples/`, and their tests (`test_api.py`, `test_catalog.py`, `test_journal.py`, `smoke_demo.py`, generic parts of `test_graph.py`).
- Dependencies no longer needed: `openapi-spec-validator`, `openapi-schema-validator`, `pyyaml`, `langgraph-checkpoint-sqlite` (Agent Server persists threads), and the `fastapi`/`uvicorn` dev dependencies that served the old demo fixture (`tests/demo_api.py`, which replaced the editable `../api` dependency in task 2).
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

## Implementation notes

- Files: `auth.py` (Agent Server auth handlers), `clerk.py` (token verification, same rules as the API), `context.py` (`AgentContext` and `signed_in_user`), `middleware/prompt.py` (`@dynamic_prompt`), `middleware/limits.py` (restored from `main`), `graph.py`.
- Verified with `langgraph dev` (langgraph-api 0.17.0) on Python 3.14, using a local JWKS and test-signed tokens: `/info` responds; thread search without a token or with an invalid one → 401; a thread created by one user → 404 for another user on read, run and delete, and absent from their search; an `owner` sent by the client is overwritten; assistant creation → 403; an unknown timezone in the run context fails the run with a validation error.
- Where the token goes: the user dict returned by `@auth.authenticate` (including `access_token`) is stored in the run record (`kwargs.config.configurable.langgraph_auth_user`), which `GET /threads/{id}/runs` returns to the thread owner only. langgraph-api treats `langgraph_auth_user` as transient for checkpoints: the token was not found in thread state or history. A request-scoped context variable would not work, because runs execute in Agent Server's queue worker, not in the request's context. Decision: keep the token in the user dict; it is the caller's own token and expires about 60 seconds after it was minted.
- Token lifetime: the frontend's LangGraph client calls `getToken({ skipCache: true })` for requests that start a run (`POST …/runs`, `/runs/stream`, `/runs/wait`), so each run starts with a token valid for about 60 seconds. Approval resumes are new runs with new tokens, and the approved write executes first in that run. Reads in a run that takes longer than the token's lifetime get 401 from the API; task 4's client reports that as an expired session so the user can ask again.
- Tools and middleware read the user from `runtime.server_info.user` (LangGraph 1.2 builds it from `langgraph_auth_user`). Runs without a Clerk user (tests, Studio) get a prompt that says nobody is signed in.
- The frontend `/chat` page was not exercised end to end in a signed-in browser here; the browser pane cannot sign in to Clerk.
