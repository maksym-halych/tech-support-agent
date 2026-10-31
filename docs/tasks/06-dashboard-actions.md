# Task 6 — Lesson actions in the dashboard

## Goal

Everything the assistant can do, users can also do in the dashboard without it: teachers open and cancel lessons, students book open lessons and cancel their bookings. The assistant stays as help for users who cannot find how to do something.

## Scope

- Teacher: a "New lesson" button next to the page title opens a form (subject, date, start time, duration). Times are entered in the browser's timezone and sent as UTC.
- Student: an "Open lessons" section under "My lessons" lists open future lessons, grouped by day, each with a Book button.
- Both roles: a Cancel button on every upcoming lesson that is not cancelled, behind a confirmation dialog that says what happens (a student's booking reopens the lesson; a teacher's cancellation is final and the student sees it).
- Every write shows a toast with the result and reloads the dashboard. The API enforces all rules, so its error message is shown as is (for example an overlapping lesson, or a lesson someone else has just booked).

## Out of scope

API changes: the endpoints and rules already exist. Changes to the assistant's tools or prompt.

## Acceptance

- A teacher opens a lesson from the form and it appears in "My schedule"; a student sees it under "Open lessons", books it, and it moves to "My lessons" and shows as booked by them for the teacher.
- Cancelling from either side behaves like the API's `cancel_lesson`.
- `pnpm lint` (no new warnings), `pnpm format:check` and `pnpm build` pass.

## Implementation notes

- `lib/lessons-api.ts`: one `request` helper for GET and POST; new `availableLessons`, `createLesson`, `bookLesson`, `cancelLesson`. FastAPI validation errors (a `detail` list) show their first message.
- `hooks/use-lessons.tsx`: also loads `available` (open lessons) for students; `refresh` reloads it, so writes made by the assistant update the list too.
- `hooks/use-lesson-write.ts`: runs one write with a fresh Clerk token, tracks it as pending, toasts the outcome and refreshes.
- `components/lessons/NewLessonDialog.tsx` (teacher form), `LessonSchedule.tsx` (`LessonSchedule` with Cancel, `AvailableLessons` with Book), `components/ui/dialog.tsx` (Radix dialog), and a sonner `Toaster` in `app/page.tsx`.
- Verified with `pnpm lint`, `pnpm format:check` and `pnpm build`. The signed-in check runs in a browser after a human signs in to Clerk.
