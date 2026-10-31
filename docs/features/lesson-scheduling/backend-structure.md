# Backend structure: lesson scheduling

| File | Content |
|---|---|
| `api/src/lessons_api/app.py` | `create_app`: endpoints, CORS, the error handler that maps rule errors to HTTP status codes |
| `api/src/lessons_api/service.py` | `LessonService`: the lesson rules and the state changes; `LessonError` and its subclasses |
| `api/src/lessons_api/storage.py` | `Store`: the SQLite schema, queries and transactions |
| `api/src/lessons_api/models.py` | Pydantic models: `Lesson`, `CreateLesson`, `Teacher`, `Person`; duration limits |
| `api/src/lessons_api/config.py` | `Settings` from `LESSONS_API_*` variables: host, port, database path |
| `api/src/lessons_api/main.py` | `lessons-api` (start the server) and `lessons-api-export-openapi` |

## Layers

1. `app.py` receives the request and gets the caller.
2. `service.py` applies the rules. It raises a `LessonError` subclass if a rule stops the action.
3. `storage.py` reads and writes SQLite. It does not apply rules.

The service gets the clock as a parameter. Tests give a fixed clock.
