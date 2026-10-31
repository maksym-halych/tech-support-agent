# tech-support-agent

PolyTech Code Fest Hackathon Project

A conversational tech support agent: a LangGraph agent served by Agent Server, with a Next.js chat frontend.

## Layout

```
agent/                      LangGraph agent (Python 3.14, uv)
  langgraph.json            Agent Server config: graph "agent" -> tech_support_agent.graph:build_agent
  src/tech_support_agent/
    graph.py                create_agent wiring: model, prompt, tools, middleware
    config.py               Settings from env / agent/.env
    prompts.py              System prompt
    middleware/             Per-turn tool call limit
    tools/                  One module per tool; the tool set lives in registry.py
  tests/
frontend/                   Next.js chat UI (Agent Chat UI fork, Clerk sign-in, pnpm)
```

## Getting started

Prerequisites: [uv](https://docs.astral.sh/uv/), Node 22 and pnpm.

1. Copy `agent/.env.example` to `agent/.env` and set `OPENAI_API_KEY` and `OPENAI_BASE_URL`.
2. Copy `frontend/.env.example` to `frontend/.env` and set the Clerk keys.
3. Install and run:

```bash
make install
make agent      # LangGraph dev server on http://localhost:2024
make frontend   # Chat UI on http://localhost:3000
```

Other targets: `make test` and `make pre-commit`.

## Bug intake boundary (Phase 1)

The ordinary `agent` remains the general support assistant. `agent/langgraph.bug-demo.json` separately registers `bug_demo`, which accepts an already-classified bug and talks to a private worker. It does not register the private graph or expose its checkpoints, tool transcripts, errors, approval or resume routes.

Set `BUG_WORKER_TOKEN` to the same operator-generated random token in both processes (at least 32 ASCII characters, without whitespace). `BUG_WORKER_URL` must be `http://127.0.0.1:<port>`; the default port is 2025. Keep these variables server-only. Run each target in a separate terminal:

```bash
make bug-worker
make bug-demo-agent
```

Both demo services are local-only. Do not expose or tunnel Agent Server: this phase does not add public Clerk verification or multi-user thread ownership. The worker authenticates every route, binds reads and answers to the source thread, and exposes only `POST /cases`, `GET /cases/{case_id}/public-event?source_thread_id=...&after=...`, and `POST /cases/{case_id}/clarification`. Answers must include the current `clarification_cursor`. Request bodies are limited to 4 MiB; at most three inline attachments are accepted, with images limited to 1 MiB decoded and four million pixels. PDF, remote image URLs and unknown command fields are rejected. Nothing fetches client-provided URLs.

Phase 1 implements real HTTP, admission, private LangGraph checkpoint/interrupt/resume and safe public messages. Investigator and team delivery doubles exist only in tests. The normal worker explicitly warns that these services are not wired, fails closed, and never invents a successful handoff. No model, Telegram, Docker or GitHub credentials are required for Phase 1 verification. Private graph tracing is disabled; runtime failures produce neutral public messages without raw exception text.

One active case holds its slot through clarification and delivery. Terminal source-thread bindings remain for the worker's lifetime, including after public checkpoint rollback. A new scenario requires a new client thread; restarting the worker discards its in-memory state and also requires a new thread. There is no persistent recovery or queue.

For future orchestration, reuse `PublicBridge(PrivateAPI(BugSettings()))` after `normalize_messages`, with `BridgeState` and the `add_messages` reducer. A trusted runtime `configurable.thread_id` is mandatory. Never take the worker URL, token or thread ID from model arguments. The public demo accepts only messages as input; its output adds opaque bridge bookkeeping. Polling is bounded; slow diagnosis returns a truthful pending notice and can reattach without creating a second case. Long-lived team/CI workflow is not implemented in this phase.

### Verification

```bash
uv sync --python 3.14 --directory agent
uv run --python 3.14 --directory agent python -m pytest tests -m 'not server and not docker and not live' -q
uv run --python 3.14 --directory agent python -m pytest tests/bug_resolution/test_intake.py tests/bug_resolution/test_bridge.py -q
uv run --python 3.14 --directory agent python -m pytest tests/bug_resolution/integration/test_public_server.py --run-server -q
uv run --python 3.14 --directory agent ruff check --select I src tests
uv run --python 3.14 --directory agent ruff format --check src tests
```

`server`, `docker` and `live` tests are opt-in via `--run-server`, `--run-docker` and `--run-live`. Offline fixtures ignore developer `.env` and block external connections. The server suite runs the locked Python SDK against a real Agent Server child process on Python 3.14, reproducing the frontend's submit/edit/regenerate payload shapes and checking state/history/SSE privacy and disconnect/rejoin. It isolates environment and server storage in a temporary directory and shuts down owned resources. This is an HTTP contract test, not a browser/Clerk or live-integration rehearsal.
