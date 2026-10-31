# Lessons platform

Three apps for the PolyTech Code Fest project: a lessons API (`api/`), a lesson assistant served by LangGraph Agent Server (`agent/`) and a Next.js/Clerk frontend (`frontend/`). Teachers open lesson slots, students book them, and either side can cancel. The refactor is planned and tracked task by task in [`docs/`](docs/README.md). `agent/` also holds a separate bug-resolution workflow; see [Bug intake boundary](#bug-intake-boundary-phase-1).

## Getting started

### Prerequisites

- [uv](https://docs.astral.sh/uv/). It installs Python 3.14 for `api/` and `agent/` if you don't have it.
- Node.js 20.9 or newer and pnpm 10 (`corepack enable` picks up the version pinned in `frontend/package.json`).
- A [Clerk](https://clerk.com) application; a development instance is enough.
- An API key for OpenAI or another OpenAI-compatible provider, such as OpenRouter.

### 1. Install dependencies

```sh
make install               # uv sync for api/ and agent/
pnpm -C frontend install
```

### 2. Configure Clerk

Once per Clerk instance, in the Clerk dashboard:

1. Sessions → Customize session token → add `{"role": "{{user.public_metadata.role}}", "name": "{{user.full_name}}", "email": "{{user.primary_email_address}}"}`.
2. API keys → note the Frontend API URL, the publishable key and the secret key for the next step.

### 3. Create the `.env` files

```sh
cp api/.env.example api/.env
cp agent/.env.example agent/.env
cp frontend/.env.example frontend/.env
```

| File | Set |
|---|---|
| `api/.env` | `LESSONS_API_CLERK_ISSUER` to the Frontend API URL |
| `agent/.env` | `CLERK_ISSUER` to the Frontend API URL, `OPENAI_API_KEY`, and `OPENAI_BASE_URL` and `AGENT_MODEL` for a provider other than OpenAI |
| `frontend/.env` | `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` and `CLERK_SECRET_KEY` |

The remaining defaults already point the apps at each other on localhost. Keep `LANGSMITH_TRACING=false` unless you have a LangSmith key. If the frontend runs on an origin other than `http://localhost:3000`, add it to `LESSONS_API_CORS_ORIGINS` and `LESSONS_API_CLERK_AUTHORIZED_PARTIES` in `api/.env` and to `CLERK_AUTHORIZED_PARTIES` in `agent/.env`.

### 4. Start the apps

Run each in its own terminal from the repository root:

```sh
make api        # lessons API on http://127.0.0.1:8001, Swagger at /docs
make agent      # Agent Server on http://127.0.0.1:2024
make frontend   # Next.js on http://localhost:3000
```

### 5. Sign in and pick a role

Open http://localhost:3000 and sign in. Every new user is a student. To try the teacher side, make a user a teacher, then have them sign out and back in so their session token carries the new role:

```sh
make users                           # list Clerk users and their roles
make teacher EMAIL=you@example.com   # make a user a teacher
make student EMAIL=you@example.com   # make them a student again
```

These commands read `CLERK_SECRET_KEY` from `frontend/.env`. You can also set the user's public metadata to `{"role": "teacher"}` in the Clerk dashboard (Users → user → Metadata).

## Lessons API

FastAPI and SQLite, package `lessons_api`. Every request needs a Clerk session token as `Authorization: Bearer`. The API verifies it against the Clerk instance's JWKS (signature, `iss`, `exp`, `nbf`, and `azp` when configured) and records the caller as a user. The token's `role` claim decides the role: only `teacher` makes a teacher, anything else is a student. See [Configure Clerk](#2-configure-clerk) for the session-token customisation.

To make a Clerk user a teacher from the CLI, run `make teacher EMAIL=...` (`make users` lists users and roles). It reads `CLERK_SECRET_KEY` from `frontend/.env`.

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

Copy `frontend/.env.example` to `frontend/.env` and set the Clerk keys, `NEXT_PUBLIC_LESSONS_API_URL` (default `http://localhost:8001`), `NEXT_PUBLIC_API_URL` (Agent Server, default `http://localhost:2024`) and `NEXT_PUBLIC_ASSISTANT_ID` (default `agent`), then `make frontend`.

- `/` is the lessons dashboard: "My lessons" for students and "My schedule" for teachers, in the browser's timezone.
- "Ask assistant" (bottom right) opens the lesson assistant in a panel on the right (full screen on phones). The dashboard stays usable next to it. The open state and the conversation are in the URL (`?chat=1&threadId=…`), so a reload restores both.
- Every booking, new lesson or cancellation the agent proposes shows an approval card with a plain-language summary. Approve runs it; Reject (with an optional reason) tells the agent nothing happened. The composer is disabled while an approval is pending.
- When a run finishes after a successful write, the dashboard reloads its lessons.
- Each run sends the browser's timezone as run context, and requests that start a run get a freshly minted Clerk token.

## Agent

One LangChain `create_agent` graph (`agent`), served by Agent Server with `langgraph dev`. It runs as the signed-in user: Agent Server's custom auth (`agent/src/tech_support_agent/auth.py`) verifies the same Clerk session token as the lessons API, and each thread and run belongs to the user who created it, so nobody else can read, search, run or delete it. Assistants are read-only for users.

```sh
cp agent/.env.example agent/.env   # OPENAI_API_KEY and CLERK_ISSUER at least
make agent                         # Agent Server on http://127.0.0.1:2024
```

- Model: any OpenAI-compatible provider (`OPENAI_API_KEY`, `OPENAI_BASE_URL`, `AGENT_MODEL`). OpenAI's own endpoint is called over the Responses API, other providers over chat completions.
- Run context: the frontend sends `context: {"timezone": "<IANA name>"}` with each run (default `Europe/Kyiv`). The system prompt adds the user's name, role, timezone and local time.
- Tools: `list_my_lessons` and `cancel_lesson` for everyone, `list_teachers`, `find_available_lessons` and `book_lesson` for students, `create_lesson` for teachers, plus `get_current_datetime`. The model only sees its role's tools, and the API enforces roles anyway. Tool calls are capped at 10 per run.
- Every `book_lesson`, `create_lesson` and `cancel_lesson` call pauses the run with a LangChain `HITLRequest` (`action_requests` with a plain-language `description`, `review_configs` allowing `approve` and `reject`). Resume the thread with `Command(resume={"decisions": [{"type": "approve"}]})` or `{"type": "reject", "message": "..."}`; nothing changes until a write is approved.
- The user's session token travels with each run so the agent's tools can call the lessons API as that user. It is never part of graph state, checkpoints or model messages; Agent Server keeps it in the run record, readable only by the thread's owner, and it expires about a minute after it was minted. The frontend mints a fresh token for every request that starts a run.

## Bug intake boundary (Phase 1)

The ordinary `agent` remains the lesson assistant. `agent/langgraph.bug-demo.json` separately registers `bug_demo`, which accepts an already-classified bug and talks to a private worker. It does not register the private graph or expose its checkpoints, tool transcripts, errors, approval or resume routes.

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

## Verify and develop

```sh
make test         # api and agent pytest
make lint         # ruff check and format check for api and agent
make pre-commit   # all hooks, including pip-audit for both projects
pnpm -C frontend lint && pnpm -C frontend format:check && pnpm -C frontend build
```

Tests run offline: the API's tests sign Clerk-shaped tokens with a throwaway RSA key, and the agent's tests use scripted chat models.
