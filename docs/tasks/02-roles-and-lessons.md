# Task 2 — Roles, lessons and the lessons dashboard

## Goal

Replace the demo booking API with a real lessons API that authenticates Clerk users and distinguishes students from teachers, and make the frontend's home page show the current user's lessons.

## Roles

- The API verifies the Clerk session JWT (`Authorization: Bearer`) against Clerk's JWKS, checking signature, `exp`, `nbf`, `iss` and, when configured, `azp` against the allowed frontend origins.
- Role comes from the `role` claim. Only `"teacher"` yields a teacher; a missing or any other value yields a student.
- Display name and email come from the `name` and `email` claims (see the Clerk setup in `docs/README.md`).
- On every authenticated request the API upserts the caller into `users` (id = `sub`, name, email, role, last_seen_at), so students can list teachers who have signed in.
- The public demo tokens `demo-alice` / `demo-bob` are removed.

## Data model (SQLite)

`users(id TEXT PK, name TEXT, email TEXT, role TEXT CHECK IN ('student','teacher'), last_seen_at TEXT)`

`lessons(id TEXT PK, teacher_id TEXT FK users, subject TEXT, starts_at TEXT UTC, duration_minutes INT, status TEXT CHECK IN ('open','booked','cancelled'), student_id TEXT NULL FK users, created_at TEXT, cancelled_at TEXT NULL)`

Lesson state machine:

```
teacher creates ──▶ open ──student books──▶ booked
                     ▲                         │
                     └──student cancels booking┘
open / booked ──teacher cancels──▶ cancelled (final)
```

Rules:

- `starts_at` must be timezone-aware and in the future when creating, booking or cancelling.
- A teacher cannot create a lesson overlapping another non-cancelled lesson of theirs (409).
- Booking a lesson already booked by the same student returns the booking unchanged (natural idempotency); booked by someone else returns 409.
- Duration 15–240 minutes; subject 1–100 characters.

## Endpoints

All endpoints require a valid JWT. Responses are Pydantic models; times are ISO 8601 with offset.

| Method | Path | Role | Purpose |
|---|---|---|---|
| GET | `/me` | any | Current user: id, name, email, role |
| GET | `/teachers` | any | Teachers known to the API |
| GET | `/lessons/mine?include_past=false` | any | Student: lessons they booked. Teacher: lessons they created, any status |
| GET | `/lessons/available?date_from=&date_to=&teacher_id=` | student | Open future lessons |
| GET | `/lessons/{id}` | owner | One lesson the caller teaches or booked |
| POST | `/lessons` | teacher | Create an open lesson `{subject, starts_at, duration_minutes}` |
| POST | `/lessons/{id}/book` | student | Book an open lesson |
| POST | `/lessons/{id}/cancel` | owner | Student: cancel own booking, lesson reopens. Teacher: cancel own lesson |

Wrong role → 403; not found or not visible to the caller → 404; state conflict → 409. CORS allows the frontend origin (`LESSONS_API_CORS_ORIGINS`, default `http://localhost:3000`).

## API settings

`LESSONS_API_CLERK_ISSUER` (Clerk frontend API URL, JWKS derived as `{issuer}/.well-known/jwks.json`), `LESSONS_API_CLERK_AUTHORIZED_PARTIES`, `LESSONS_API_CORS_ORIGINS`, `LESSONS_API_DATABASE_PATH`. Add `api/.env.example`.

Dependency: `pyjwt[crypto]` for verification with a cached `PyJWKClient`.

## Frontend

- Home page `/` becomes the lessons dashboard. The existing full-page chat moves out of the way temporarily (kept on `/chat` until task 5 replaces it with the panel).
- `src/lib/lessons-api.ts`: typed client (zod schemas mirroring the API models) calling `NEXT_PUBLIC_LESSONS_API_URL` with a fresh Clerk token per request (same pattern as `providers/client.ts`).
- `src/hooks/use-lessons.ts`: loads `/me` and `/lessons/mine`, exposes `refresh()` for task 5.
- Student view: "My lessons" — upcoming booked lessons with teacher, subject, local date/time, duration.
- Teacher view: "My schedule" — upcoming lessons grouped by day, each with status (open / booked by <student name> / cancelled).
- Header with user name, role badge and Clerk `UserButton`. Empty, loading and error states. Times rendered in the browser's timezone.
- `frontend/.env.example`: `NEXT_PUBLIC_LESSONS_API_URL=http://localhost:8001`.

## Tests

- `api/tests`: sign tokens with a test RSA key and override the JWKS dependency. Cover role defaulting, every endpoint's happy path, 403/404/409 cases, overlap detection, booking idempotency, cancel semantics per role, past-lesson rules.
- Frontend: `pnpm lint` and `pnpm build` pass.

## Acceptance criteria

- A student sees only lessons they booked; a teacher sees only lessons they teach.
- Changing a user's `publicMetadata.role` to `teacher` switches the dashboard to the teacher view after the session token refreshes.
- Calls without a valid Clerk token get 401.

## Out of scope

Booking or cancelling buttons in the dashboard (the agent does writes in tasks 4–5). Agent changes.
