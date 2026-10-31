# Lessons platform refactor

This folder plans the refactor of `feat/ai-support-agent` into three apps: a lessons API, an Agent Server agent and a Next.js frontend with a side chat. Each task has its own file under `tasks/` and is implemented and committed separately, in order.

## Target architecture

```
                 Clerk (sign-in, session JWT with `role` claim)
                        │
                        ▼
frontend/  Next.js :3000 ─────────────── Bearer JWT ──────────────┐
  • lessons dashboard (student / teacher view)                    │
  • right-side chat panel (LangGraph SDK, useStream)              │
        │                                                         │
        │ Bearer JWT                                              ▼
        ▼                                              api/  FastAPI :8001
agent/  Agent Server :2024  ─── tools forward the user's JWT ───▶  • verifies Clerk JWT
  • custom auth verifies Clerk JWT                                 • lessons, bookings, roles
  • one create_agent with typed lesson tools                       • SQLite storage
  • HumanInTheLoopMiddleware approves every write
```

## Decisions

| Topic | Decision |
|---|---|
| Lesson model | Teachers open lesson slots. A student "creates a lesson" by booking an open slot. A student cancels their booking and the slot reopens; a teacher cancels the lesson itself. |
| Roles | `role` claim in the Clerk session token, sourced from the user's `publicMetadata.role`. Only the exact value `teacher` makes a teacher; anything else, including a missing claim, is `student`. |
| Agent | One `create_agent` served by Agent Server, with typed tools that call the lessons API. No subagent. The generic OpenAPI catalog, HTTP executor, dispatch journal and CLI from this branch are removed. |
| Write approval | `HumanInTheLoopMiddleware` interrupts before every create, book or cancel. The chat panel renders an approve/reject card. |
| Authentication | The Clerk JWT is the only identity. The API and Agent Server both verify it against Clerk's JWKS. The agent forwards the caller's token to the API, so the API enforces the same permissions for UI and agent calls. The model never sees the token. |
| Python | 3.14 for `agent/` and `api/`. `langgraph-cli[inmem]` 0.4.40 / `langgraph-api` 0.17.0 install on 3.14. |
| Commits | One Conventional Commit per task on `feat/ai-support-agent`, not pushed. |

## Tasks

| # | Task | Depends on | Status |
|---|---|---|---|
| 1 | [Extract the API into `api/`](tasks/01-extract-api.md) | — | Planned |
| 2 | [Roles, lessons and the lessons dashboard](tasks/02-roles-and-lessons.md) | 1 | Planned |
| 3 | [Back to Agent Server with Clerk auth](tasks/03-agent-server.md) | 2 | Planned |
| 4 | [Lesson tools with write approval](tasks/04-lesson-tools.md) | 3 | Planned |
| 5 | [Right-side chat panel](tasks/05-chat-panel.md) | 4 | Planned |

## Local setup after the refactor

| App | Command | URL |
|---|---|---|
| Lessons API | `make api` | http://localhost:8001 (Swagger at `/docs`) |
| Agent Server | `make agent` | http://localhost:2024 |
| Frontend | `make frontend` | http://localhost:3000 |

Clerk dashboard setup, done once per Clerk instance:

1. Sessions → Customize session token → add `{"role": "{{user.public_metadata.role}}", "name": "{{user.full_name}}", "email": "{{user.primary_email_address}}"}`.
2. To make a user a teacher, set their public metadata to `{"role": "teacher"}` (Users → user → Metadata). Every other user is a student.

## Verification notes

Signing in goes through Clerk's hosted frontend API, so a human signs in in the browser pane before UI checks. Everything else (tests, lint, API calls with test-signed JWTs) runs without Clerk.
