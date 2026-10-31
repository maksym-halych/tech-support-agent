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

Phase 1 implements real HTTP, admission, private LangGraph checkpoint/interrupt/resume and safe public messages. Investigator and team delivery doubles exist only in tests. The current worker connects the real investigator, Telegram approval, isolated fixing, independent verification and optional GitHub publication; see the [operator runbook](docs/bug-resolution-demo.md). No model, Telegram, Docker or GitHub credentials are required for offline Phase 1 verification. Private graph tracing is disabled; runtime failures produce neutral public messages without raw exception text.

One active case holds its slot through clarification and delivery. Terminal source-thread bindings remain for the worker's lifetime, including after public checkpoint rollback. A new scenario requires a new client thread; restarting the worker discards its in-memory state and also requires a new thread. There is no persistent recovery or queue.

For future orchestration, reuse `PublicBridge(PrivateAPI(BugSettings()))` after `normalize_messages`, with `BridgeState` and the `add_messages` reducer. A trusted runtime `configurable.thread_id` is mandatory. Never take the worker URL, token or thread ID from model arguments. The public demo accepts only messages as input; its output adds opaque bridge bookkeeping. Polling is bounded; slow diagnosis returns a truthful pending notice and can reattach without creating a second case. Team decisions and CI waits remain private and do not keep the client run open.

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

## Isolated Demo Sandbox (Phase 2)

Phase 2 adds Docker/browser/file adapters and operator staging assets. The current worker uses those adapters in the integrated approval, fix and publication workflow. Production runs do not use the deterministic test doubles.

The React/TypeScript/Vite quiz has two independent source trees: `demo/continue` blocks Continue after selecting a grade but preserves both subjects in summary logic; `demo/both` allows the transition but loses Reading in the final choices. The latter does not choose a future learning-plan design. Neither tree contains a fix patch or a ready-made bug regression test. The common smoke suite tests functioning behavior rather than asserting a bug as expected behavior.

### Operator Provisioning

Set `BUG_DEMO_REPO=owner/name` and `BUG_DEMO_REPO_DIR` to an absolute external directory in `agent/.env` or the process environment. These are operator inputs: the commands never invent a remote, initialize Git, commit, push, or create a GitHub repository. `BUG_SANDBOX_IMAGE` defaults to the explicitly built local image `bug-resolution-sandbox:phase2`.

1. Choose the actual demo repository and its external local directory. Create its parent directory yourself. Do not put the demo inside this support repository.
2. Stage `continue` into an empty directory (an empty operator-created Git worktree containing only `.git` is also accepted). Review the generated source. The operator is responsible for recording it on `demo/continue`.
3. Stage `both` into a second empty, operator-selected worktree of the same demo repository, setting `BUG_DEMO_REPO_DIR` to that worktree for this command. Record it on `demo/both`. Staging refuses to overwrite an existing source tree; it never switches branches or removes files.
4. Point `BUG_DEMO_REPO_DIR` at the external repository containing both local branch refs. Remote setup and any later push require separate operator action. No GitHub credentials are needed for local preview or Docker tests.

```bash
# Configuration check only; values are not printed, files are not written.
uv run --python 3.14 --directory agent python -m tech_support_agent.bug_resolution.demo --scenario continue
# Explicit staging; run for each scenario in its configured empty worktree.
uv run --python 3.14 --directory agent python -m tech_support_agent.bug_resolution.demo --scenario continue --write
uv run --python 3.14 --directory agent python -m tech_support_agent.bug_resolution.demo --scenario both --write
# Trusted image preparation, never available to the model.
uv run --python 3.14 --directory agent python -m tech_support_agent.bug_resolution.sandbox --build-image
# Preview captures the configured branch SHA and prints a loopback URL. Ctrl-C cleans up.
uv run --python 3.14 --directory agent python -m tech_support_agent.bug_resolution.sandbox --preview continue
```

Preview does not run the public agent or change source files. Review desktop/mobile appearance and the explicit demo-only final confirmation separately. The image pins Node 24.11.0 and Playwright 1.56.1 / Chromium build 1194; pnpm lockfiles fix dependency resolution. Image preparation needs access to public package/browser registries. If Docker's personal credential helper stalls, use an operator-created temporary empty `DOCKER_CONFIG` and the local daemon's `DOCKER_HOST`; do not delete or overwrite your normal Docker configuration. The integration fixture handles this with a temporary CLI config and requires a local Unix-socket daemon.

### Sandbox Guarantees And Limits

The adapter exports a captured Git SHA without checking out or executing the demo on the host. It validates regular files and exact trusted harness contents, then copies them over bounded JSON-lines into a non-root, read-only-rootfs container. There are no host mounts, Docker socket, service tokens, privileged mode, or host networking; CPU, memory, PID, tmpfs, output, and time budgets are fixed. Host access is limited to the loopback-published demo port. A timeout destroys the container or kills the fixed check's process group, including descendants. A fresh case gets a new workspace.

Model-facing tools only read/search source and inspect/navigate/click/select/screenshot the demo. Paths, snapshot refs, operation schemas, response IDs, and PNG exports are validated. There is no shell, arbitrary JavaScript, URL fetch, write, or check-command argument tool. Browser requests allow only the demo origin; all HTTP redirects are conservatively rejected because Playwright does not re-run route interception for every redirect hop. WebSockets, service workers, popups, and downloads are disabled. This is not full egress isolation for arbitrary test code, nor a multi-tenant untrusted-code platform. Trust the prepared base; no production endpoints or personal data belong in it.

```bash
uv run --python 3.14 --directory agent python -m pytest tests/bug_resolution/test_sandbox_policy.py -q
uv run --python 3.14 --directory agent python -m pytest tests/bug_resolution/integration/test_demo_sandbox.py --run-docker -q
```

The offline policy suite also runs trusted Node policy code; Node is required, but it never executes demo source on the host. Docker tests build the real image, create temporary isolated Git object fixtures for both branches, run Chromium and fixed typecheck/smoke/build checks, validate PNGs and isolation, exercise redirect blocking and timeout cleanup, and remove their containers. Fixture-only Git commits have no remote and do not modify this repository. The demo CI workflow has read-only permissions, `tests`/`build` jobs, and PR filters for both bases; a real GitHub Actions run remains Phase 5 work.

## Full Demo Rehearsal

Use [docs/bug-resolution-demo.md](docs/bug-resolution-demo.md) for preflight, safe launch order, real Continue/Both human decisions, evidence inspection, shutdown, and the authorized Clerk Playwright harness. `make bug-preflight` checks configured targets without sending a team message or creating a PR. Live rehearsal additionally requires explicit publication authorization and `make bug-rehearsal SCENARIO=continue LIVE=--run-live` (then a fresh `both` run). The runner never submits a report or approves its own plan. Missing configuration or credentials blocks live verification, not offline tests.
