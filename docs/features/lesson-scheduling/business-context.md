# Business context: lesson scheduling

## Users

- A **teacher** opens lesson slots and cancels lessons.
- A **student** finds open lessons, books them and cancels bookings.

## Rules

All rules are in `api/src/lessons_api/service.py`.

1. Only a teacher can open a lesson. The teacher of the lesson is the caller.
2. A lesson must start in the future. The duration is 15 to 240 minutes; the default is 60. The subject has 1 to 100 characters.
3. A new lesson must not overlap a lesson of the same teacher that is not cancelled.
4. Only a student can search for open lessons and book them.
5. A student can book only an open lesson that has not started.
6. A booking must not overlap another booking of the same student.
7. If a student books the same lesson again, the API returns the same booking.
8. A student can cancel a booking before the lesson starts. The lesson becomes open again.
9. A teacher can cancel a lesson before it starts. This cancellation is final. A second cancellation returns the lesson unchanged.
10. A student cannot cancel a lesson that the teacher cancelled.
11. A teacher sees only their own lessons. A student sees their bookings and the open future lessons.
12. If the caller cannot see a lesson, the API returns "not found". It does not show that the lesson exists.
13. "My lessons" shows only lessons that have not ended, unless the request sets `include_past=true`.

## Time

The API keeps and returns times in UTC. Each input time must have an explicit UTC offset. The dashboard shows times in the browser timezone.

## Reasons

- The API is the only place for rules. The dashboard and the agent get the same behavior.
- A repeated booking request is safe. A retry after a network error does not make a second booking.
