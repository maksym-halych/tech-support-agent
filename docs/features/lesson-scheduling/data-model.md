# Data model: lesson scheduling

## `lessons` table (SQLite, `api/src/lessons_api/storage.py`)

| Column | Type | Note |
|---|---|---|
| `id` | TEXT, primary key | UUID |
| `teacher_id` | TEXT, not null | References `users.id` |
| `subject` | TEXT, not null | 1 to 100 characters |
| `starts_at` | TEXT, not null | UTC, ISO 8601, second precision |
| `duration_minutes` | INTEGER, not null | `CHECK` 15 to 240 |
| `status` | TEXT, not null | `open`, `booked` or `cancelled` |
| `student_id` | TEXT | References `users.id`; empty for an open lesson |
| `created_at` | TEXT, not null | UTC |
| `cancelled_at` | TEXT | UTC; set when the teacher cancels |

Indexes: `(teacher_id, starts_at)`, `(student_id, starts_at)`, `(status, starts_at)`.

## `Lesson` response (`api/src/lessons_api/models.py`)

`id`, `subject`, `starts_at`, `ends_at` (calculated), `duration_minutes`, `status`, `teacher` (`id`, `name`), `student` (`id`, `name`, or null), `created_at`, `cancelled_at`.

## `CreateLesson` request

| Field | Rule |
|---|---|
| `subject` | 1 to 100 characters, spaces removed at the ends |
| `starts_at` | Time with a UTC offset; must be in the future |
| `duration_minutes` | 15 to 240; default 60 |

Unknown fields cause a 422 error.

## Copies of the model

The same lesson shape is in three places. Change all three together:

- `api/src/lessons_api/models.py` (source)
- `frontend/src/lib/lessons-api.ts` (zod schema)
- `agent/src/tech_support_agent/lessons/models.py` (agent client)
