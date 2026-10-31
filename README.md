# OpenAPI support agent

Python 3.14 / LangGraph support subgraph for the PolyTech Code Fest project. The agent discovers capabilities from a local OpenAPI document, gathers parameters in conversation, executes API reads, and pauses for separate human approval before every write. Lesson booking is a demonstration API; the graph has no booking-specific tools.

## Run the local demo

Install [uv](https://docs.astral.sh/uv/), then copy `agent/.env.example` to `agent/.env`. Set `OPENAI_API_KEY` for your provider; `OPENAI_BASE_URL` and `AGENT_MODEL` retain their existing OpenAI defaults and also support OpenRouter or other compatible providers. Python 3.14 is selected by uv.

```sh
uv sync --directory agent
# Terminal 1: persistent local booking API on 127.0.0.1:8001
uv run --directory agent support-demo-api
# Terminal 2: interactive chat
uv run --directory agent support-chat
```

Equivalent Make targets: `make install`, `make demo-api`, `make agent`. The CLI prints its thread ID. Resume it with `uv run --directory agent support-chat --thread-id <id>`. Use `/exit` to leave a conversation. A pending write offers `approve`, `reject`, or `exit`; exiting preserves the pending action. To change a choice, reject it first and send a new message.

Try “Запиши мене на урок наступного вівторка о 14:00”. The API offers two teachers with weekday slots at 10:00, 14:00 and 16:00 in `Europe/Kyiv`. The agent queries slots, asks you to choose, presents the exact action, waits for approval, and returns the booking result. Dates in the demo are generated relative to the API clock, so future weekdays continue to work.

The local API uses deliberately public test tokens `demo-alice` and `demo-bob`; set both `SUPPORT_USER_ID` and `SUPPORT_API_TOKEN` when switching users and start a new thread. These tokens are only for this loopback demonstration. Swagger UI is at `http://127.0.0.1:8001/docs`. Demo bookings, graph checkpoints and dispatch receipts persist separately under `agent/data/`, which Git ignores.

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
uv run --directory agent pytest tests -q
uv run --directory agent ruff check src tests
uv run --directory agent ruff format --check src tests
uv run --directory agent pip-audit
# Real loopback HTTP and CLI recovery, with a scripted model (no API key):
uv run --directory agent python -m tests.smoke_demo
# Refresh the example contract after changing the demo API:
uv run --directory agent support-export-openapi
```

Tests use scripted models and isolated HTTP transports without external keys. They cover approval and rejection, stale approval, SQLite recovery, ownership, idempotency and conflicts, uncertain writes, credential handling, schema validation, timezones, tool limits, parent composition, and a second inventory API contract.

`agent/src/tech_support_agent/` contains the graph, typed boundaries, OpenAPI catalog, HTTP executor, dispatch journal and CLI; `demo/` contains the booking service. `agent/examples/lessons.openapi.json` is exported from that service. The existing Next.js/Clerk `frontend/` is retained for the future orchestrator. This version does not run Agent Server or expose a frontend backend; the orchestrator, PR agent and chat integration are separate work.
