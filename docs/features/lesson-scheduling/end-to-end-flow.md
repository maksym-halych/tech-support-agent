# End-to-end flow: lesson scheduling

## Flow 1: load the dashboard

1. `LessonsProvider` (`frontend/src/hooks/use-lessons.tsx`) calls `GET /me` and `GET /lessons/mine`.
2. For a student, it also calls `GET /lessons/available?limit=200`.
3. `Dashboard` shows "My lessons" (student) or "My schedule" (teacher).

## Flow 2: a teacher opens a lesson

1. The teacher clicks the new lesson button. `NewLessonDialog` opens.
2. The teacher enters the subject, the start date and time and the duration.
3. The frontend sends `POST /lessons` with `starts_at` in ISO 8601 with an offset.
4. The API verifies the token and the teacher role.
5. The service changes the start time to UTC and makes sure that it is in the future.
6. In one transaction, the service checks for an overlap and inserts the lesson with status `open`.
7. The API returns 201 with the lesson.
8. `useLessonWrite` shows a toast and loads the lessons again.

## Flow 3: a student books a lesson

1. The student clicks **Book** on an open lesson.
2. The frontend sends `POST /lessons/{lesson_id}/book`.
3. In one transaction, the service checks the status, the start time and the overlap with other bookings.
4. The store sets the status to `booked` and the student ID.
5. The API returns the lesson. The dashboard shows a toast and loads the lessons again.

## Flow 4: a student cancels a booking

1. The student clicks **Cancel** on a booked lesson.
2. The frontend sends `POST /lessons/{lesson_id}/cancel`.
3. The service sets the status to `open` and removes the student.

## Flow 5: a teacher cancels a lesson

1. The teacher clicks **Cancel** on a lesson.
2. The frontend sends `POST /lessons/{lesson_id}/cancel`.
3. The service sets the status to `cancelled` and the cancellation time. It keeps the student.

## Lesson states

```
open    --book (student)-->    booked
booked  --cancel (student)-->  open
open    --cancel (teacher)-->  cancelled  (final)
booked  --cancel (teacher)-->  cancelled  (final)
```

## Errors

| HTTP status | Cause | Example message |
|---|---|---|
| 401 | No valid session token | Invalid or missing session token |
| 403 | The role cannot do the action | Only teachers can create lessons |
| 404 | The lesson does not exist or the caller cannot see it | Lesson not found |
| 409 | The lesson state does not allow the action | Someone else has already booked this lesson |
| 422 | A value is not valid | A lesson must start in the future |

The dashboard shows the API message in a toast without changes.
