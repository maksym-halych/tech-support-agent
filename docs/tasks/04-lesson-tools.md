# Task 4 — Lesson tools with write approval

## Goal

Give the agent typed tools to read lessons and to create, book and cancel them through the lessons API, acting strictly as the signed-in user, with human approval before every write.

## API client

`agent/src/tech_support_agent/lessons/client.py`:

- `LessonsClient` wrapping one `httpx.AsyncClient` per call, base URL from `LESSONS_API_URL`, 10 s timeout, no redirects, `trust_env=False`.
- Sends the caller's Clerk JWT as `Authorization: Bearer`; the token is read from the auth user (see task 3), never from tool arguments.
- Pydantic models mirroring the API (`Lesson`, `Teacher`, `Me`), in `lessons/models.py`.
- Maps errors to one `LessonsApiError(status, message)`: 401/403/404/409/422 keep the API's `detail`; network errors and 5xx get a generic message. Reads may retry once on network error; writes never retry.

## Tools

One module per tool under `tools/`, each with a Pydantic input schema and a Google-style docstring the model reads.

| Tool | Role | API call | Approval |
|---|---|---|---|
| `get_current_datetime` | any | — (existing, now uses run timezone) | no |
| `list_my_lessons(include_past)` | any | `GET /lessons/mine` | no |
| `list_teachers()` | student | `GET /teachers` | no |
| `find_available_lessons(date_from, date_to, teacher_id?)` | student | `GET /lessons/available` | no |
| `book_lesson(lesson_id)` | student | `POST /lessons/{id}/book` | yes |
| `create_lesson(subject, starts_at, duration_minutes)` | teacher | `POST /lessons` | yes |
| `cancel_lesson(lesson_id)` | any | `POST /lessons/{id}/cancel` | yes |

- Tools return compact JSON (Pydantic `model_dump(mode="json")`) with times converted to the run timezone alongside UTC.
- `LessonsApiError` becomes a tool error message the model can explain (e.g. "That slot was just booked by someone else").

## Role-based tool selection

A `wrap_model_call` middleware filters the tool list by the caller's role before each model call, so students never see `create_lesson` and teachers never see `book_lesson`, `find_available_lessons` or `list_teachers`. The API still enforces roles; this only keeps the model from proposing impossible actions.

## Write approval

`HumanInTheLoopMiddleware(interrupt_on={"book_lesson": ..., "create_lesson": ..., "cancel_lesson": ...})` with `allowed_decisions=["approve", "reject"]` and a `description` factory that renders a human-readable summary (lesson subject, teacher, local date/time, duration) from the tool arguments. For `book_lesson` and `cancel_lesson` the description fetches the lesson first so the user sees what they approve, not just an ID.

Interrupt payload (`HITLRequest`): `{"action_requests": [{"name", "args", "description"}], "review_configs": [{"action_name", "allowed_decisions"}]}`. Resume with `Command(resume={"decisions": [{"type": "approve"} | {"type": "reject", "message": "..."}]})`, one decision per action request. Task 5 builds the UI for this.

## Prompt

Update the system prompt with the workflow: list or search first, show options with local times, ask the user to choose, then call the write tool; after a rejection, ask what to change; never invent lesson IDs.

## Tests

- Client: `httpx.MockTransport` for success, each error status, network error, read retry, no write retry, token header present and never in tool output.
- Tools: role filtering middleware per role; each tool maps arguments to the right request.
- Graph: with a scripted fake model, a write tool call pauses with the expected `HITLRequest`; approve executes the API call once; reject executes nothing and returns the rejection to the model.

## Acceptance criteria

- In `/chat`, a student can ask "book me a math lesson tomorrow afternoon", pick a slot, approve, and see the lesson in the dashboard after refresh.
- A teacher can ask "open a 60-minute English lesson on Friday at 10:00", approve, and see it in their schedule.
- Either role can cancel through the agent with approval; rejecting leaves the lesson unchanged.
- `pytest`, `ruff check`, `ruff format --check` pass.
