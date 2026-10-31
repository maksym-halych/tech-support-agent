# Lesson scheduling

## Summary

Teachers open lessons. Students find open lessons, book them and cancel their bookings. Teachers cancel their lessons. The lessons dashboard shows the lessons of the user. The lessons API applies all the rules.

## Scope

- The lessons API endpoints and the lesson rules.
- The lessons dashboard: student view and teacher view.
- SQLite storage of lessons.

Sign-in is not in scope. See [sign-in-and-roles](../sign-in-and-roles/FEATURE.md). The chat agent uses the same endpoints. See [lesson-assistant](../lesson-assistant/FEATURE.md).

## Glossary

| Term | Meaning |
|---|---|
| Lesson | A time slot that a teacher opens: subject, start time, duration. |
| Open lesson | A lesson with status `open`. A student can book it. |
| Booking | A student holds a lesson. The lesson status is `booked`. |
| Cancelled lesson | The teacher cancelled the lesson. This status is final. |
| Overlap | Two lessons that share time: each starts before the other ends. |
| My lessons | The bookings of a student, or the lessons of a teacher. |

## Status

Implemented. Tests: `api/tests/test_lessons.py`.
