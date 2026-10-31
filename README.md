# Lessons platform

Three apps for the PolyTech Code Fest project: a lessons API (`api/`), a Next.js/Clerk frontend (`frontend/`) and a LangGraph agent (`agent/`). Teachers open lesson slots, students book them, and either side can cancel. The refactor that turns the agent into an Agent Server lesson assistant with a side chat is planned task by task in [`docs/`](docs/README.md).

## Lessons API

FastAPI and SQLite, package `lessons_api`. Every request needs a Clerk session token as `Authorization: Bearer`. The API verifies it against the Clerk instance's JWKS (signature, `iss`, `exp`, `nbf`, and `azp` when configured) and records the caller as a user. The token's `role` claim decides the role: only `teacher` makes a teacher, anything else is a student. See [docs/README.md](docs/README.md#local-setup-after-the-refactor) for the Clerk session-token customisation.

```sh
cp api/.env.example api/.env   # set LESSONS_API_CLERK_ISSUER to your Clerk frontend API URL
make api                       # http://127.0.0.1:8001, Swagger at /docs
```

| Method | Path | Who | Purpose |
|---|---|---|---|
| GET | `/me` | anyone | Current user and role |
| GET | `/teachers` | anyone | Teachers who have signed in |
| GET | `/lessons/mine?include_past=false` | anyone | Student: lessons they booked. Teacher: lessons they created |
| GET | `/lessons/available?starts_after=&starts_before=&teacher_id=&limit=` | student | Open future lessons |
| GET | `/lessons/{id}` | teacher of it, its student, or any student while it is open | One lesson |
| POST | `/lessons` | teacher | Open a lesson `{subject, starts_at, duration_minutes}` (15–240 min, future, no overlap) |
| POST | `/lessons/{id}/book` | student | Book an open lesson; repeating it returns the same booking |
| POST | `/lessons/{id}/cancel` | student or teacher | Student: cancel the booking, the lesson reopens. Teacher: cancel the lesson |

Missing or invalid token → 401, wrong role → 403, unknown or invisible lesson → 404, state conflict (already booked, cancelled, started, overlapping) → 409. Data lives in `api/data/lessons.sqlite`, which Git ignores.

## Frontend

Copy `frontend/.env.example` to `frontend/.env`, set the Clerk keys and `NEXT_PUBLIC_LESSONS_API_URL` (default `http://localhost:8001`), then `make frontend`. After signing in, `/` shows the lessons dashboard: "My lessons" for students and "My schedule" for teachers, in the browser's timezone. The full-page agent chat is on `/chat` until the side panel replaces it.

## Legacy OpenAPI support agent

The rest of this README describes the generic OpenAPI support subgraph that is still in `agent/`. It is removed in [task 3](docs/tasks/03-agent-server.md). Its demo booking API now lives only in `agent/tests/demo_api.py` as a test fixture, so the CLI (`make agent`) has no live API to talk to; `uv run --directory agent python -m tests.smoke_demo` runs the whole flow over real loopback HTTP with a scripted model.

## Configure another API

Set `SUPPORT_OPENAPI_PATH` to its local OpenAPI 3.0/3.1 JSON or YAML file and `SUPPORT_API_BASE_URL` to its trusted base URL. Paths in `.env` are relative to the process working directory; the commands above use `agent/`. The specification is loaded and validated when the graph is built. Start a new thread after changing the contract, API origin or idempotency configuration; saved approvals cannot be reused for another integration.

- Supported: JSON request/response bodies; scalar path parameters; scalar or flat-array query/header parameters with OpenAPI's default `form`/`simple` serialization; nonrecursive internal `$ref`; Bearer and header API-key security schemes. Missing operation IDs receive a stable `METHOD /path` identifier.
- Unsupported: external or recursive references, multipart/form/XML bodies, cookie/object parameters, nondefault serialization and OAuth token acquisition. Invalid documents fail at load; unsupported operations are marked in the catalog and cannot send requests. Tokens for supported security schemes must already be supplied by the authenticated caller.
- The configured origin wins over `servers` in the specification. The model chooses an operation ID and documented parameters, never a URL or credentials. Redirects are not followed. Responses are validated, limited to 1 MiB and scrubbed of reflected credentials.
- GET/HEAD are reads. Every other supported method requires approval. Read timeouts and temporary server errors get at most two retries; writes get no automatic retries. Requests time out after 20 seconds; each run allows ten tool calls.
- Set `SUPPORT_IDEMPOTENCY_HEADER` to the API's supported header; leave it empty for APIs without idempotency support. The runtime generates its value from the approved action ID. It is never a model argument.

## Embed the subgraph

The public factory is `tech_support_agent.build_support_graph(model, api_config, checkpointer=None, *, transport=None, journal=None)`. `transport` supports offline HTTP tests. Supply a durable `ActionJournal` whenever the process may restart; its default is in-memory. The application owns and closes the journal.

```python
from pathlib import Path

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command
from pydantic import SecretStr

from tech_support_agent import ApiConfig, ApiCredentials, SupportContext, build_support_graph
from tech_support_agent.config import get_settings
from tech_support_agent.graph import create_chat_model
from tech_support_agent.journal import ActionJournal
from tech_support_agent.models import CHECKPOINT_TYPES

# Run from agent/; ensure data/ exists.
context = SupportContext(
    user_id="alice",
    timezone="Europe/Kyiv",
    credentials=ApiCredentials(bearer_token=SecretStr("demo-alice")),
)
api = ApiConfig(
    openapi_path=Path("examples/lessons.openapi.json"),
    base_url="http://127.0.0.1:8001",
    idempotency_header="Idempotency-Key",
)
journal = ActionJournal(Path("data/actions.sqlite"))
try:
    async with AsyncSqliteSaver.from_conn_string("data/support.sqlite") as saver:
        saver.serde = JsonPlusSerializer(allowed_msgpack_modules=CHECKPOINT_TYPES)
        graph = build_support_graph(create_chat_model(get_settings()), api, saver, journal=journal)
        config = {"configurable": {"thread_id": "alice-example"}}
        result = await graph.ainvoke({"messages": [HumanMessage("Show my bookings")]}, config, context=context)
        # For writes, render result["__interrupt__"][0].value to the user.
        # Only after explicit human approval, resume the SAME thread with:
        # Command(resume={"action_id": approval["action_id"], "decision": "approve"})
finally:
    journal.close()
```

For API-key authentication, populate `ApiCredentials(api_keys={"SecuritySchemeName": SecretStr("...")})`; keys are OpenAPI security scheme names. A context can supply both supported credential types to satisfy combined security requirements. Credentials are excluded from serialization and are not placed in the prompt or checkpoint state.

The input channel is `messages`. Output channels are `messages` and a Pydantic `SupportResult` with `status`, `message` and optional `data`. Statuses are `completed`, `needs_input`, `unsupported`, `failed`, and `unknown_outcome`. Pending confirmation uses LangGraph interrupts. Resume values accept exactly `action_id` and `decision` (`approve` or `reject`); stale IDs or extra fields pause again without executing HTTP.

For a multi-turn parent graph, build the child with `checkpointer=True`, add it as a node, and compile the parent with its durable saver. This keeps the child's private state across parent invocations while using the parent's storage. `checkpointer=None` inherits persistence for a single child invocation. Pass `SupportContext` on each parent run and resume. Use a shared `messages` channel with `add_messages`, and a `result` channel for the child's result. Serialize runs per thread; do not invoke the same stateful child concurrently in one thread.

Configure the parent's checkpoint serializer with `CHECKPOINT_TYPES`, as in the standalone example, so strict deserialization can reconstruct the typed result, API response and pending action. Add the parent's own state types to that allowlist if needed.

The embedding application authenticates users and checks thread ownership **before** reading checkpoints, streaming history, submitting input or resuming a run. The graph also checks its stored owner before model/API execution, but it is not an HTTP authentication boundary. Do not expose state-editing or arbitrary `Command(update=...)` access to untrusted clients. The CLI checks ownership before displaying history. The future orchestrator must provide the same access control.

Dispatch receipts are committed before sending writes. An interrupted dispatch without a saved response becomes `unknown_outcome`; the runtime never silently repeats it. A remote API without idempotency cannot offer exactly-once delivery. Read the remote state or reconcile externally before trying again. The same uncertain request remains blocked within the thread; unresolved writes keep its result marked `unknown_outcome`. Preserve both checkpoint and journal files across restarts, and do not fork historical checkpoints to replay side effects.

## Verify and develop

```sh
uv run --directory api pytest tests -q
uv run --directory agent pytest tests -q
uv run --directory agent ruff check src tests
uv run --directory agent ruff format --check src tests
uv run --directory agent pip-audit
# Real loopback HTTP and CLI recovery, with a scripted model (no API key):
uv run --directory agent python -m tests.smoke_demo
```

Tests use scripted models and isolated HTTP transports without external keys. They cover approval and rejection, stale approval, SQLite recovery, ownership, idempotency and conflicts, uncertain writes, credential handling, schema validation, timezones, tool limits, parent composition, and a second inventory API contract.

`api/` is the lessons API (FastAPI, SQLite, package `lessons_api`), a separate uv project with its own tests. `agent/src/tech_support_agent/` contains the graph, typed boundaries, OpenAPI catalog, HTTP executor, dispatch journal and CLI. `agent/examples/lessons.openapi.json` is the contract of the test demo in `agent/tests/demo_api.py`.
