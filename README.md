# Lessons platform

Three apps for the PolyTech Code Fest project: a lessons API (`api/`), a lesson assistant served by LangGraph Agent Server (`agent/`) and a Next.js/Clerk frontend (`frontend/`). Teachers open lesson slots, students book them, and either side can cancel. The refactor is planned and tracked task by task in [`docs/`](docs/README.md).

## Lessons API

FastAPI and SQLite, package `lessons_api`. Every request needs a Clerk session token as `Authorization: Bearer`. The API verifies it against the Clerk instance's JWKS (signature, `iss`, `exp`, `nbf`, and `azp` when configured) and records the caller as a user. The token's `role` claim decides the role: only `teacher` makes a teacher, anything else is a student. See [docs/README.md](docs/README.md#local-setup-after-the-refactor) for the Clerk session-token customisation.

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

## Run everything

Do the Clerk setup in [docs/README.md](docs/README.md#local-setup-after-the-refactor) once, fill in the three `.env` files, then in three terminals:

```sh
make api        # lessons API on :8001
make agent      # Agent Server on :2024
make frontend   # Next.js on :3000
```

## Verify and develop

```sh
make test         # api and agent pytest
make lint         # ruff check and format check for api and agent
make pre-commit   # all hooks, including pip-audit for both projects
pnpm -C frontend lint && pnpm -C frontend format:check && pnpm -C frontend build
```

Tests run offline: the API's tests sign Clerk-shaped tokens with a throwaway RSA key, and the agent's tests use scripted chat models.
