# Integration points: lesson scheduling

## Other features

- [sign-in-and-roles](../sign-in-and-roles/FEATURE.md): each endpoint needs a caller. The role selects the allowed actions.
- [lesson-assistant](../lesson-assistant/FEATURE.md): the agent tools call these endpoints with the token of the user. After a write by the agent, the chat panel loads the dashboard lessons again.

## Contract

- The API publishes OpenAPI at `/openapi.json` and Swagger at `/docs`.
- `lessons-api-export-openapi --output <file>` writes the contract to a file.

## Configuration

| Variable | Default | Use |
|---|---|---|
| `LESSONS_API_HOST` | `127.0.0.1` | Bind address |
| `LESSONS_API_PORT` | `8001` | Port |
| `LESSONS_API_DATABASE_PATH` | `data/lessons.sqlite` | SQLite file, relative to `api/` |
| `LESSONS_API_CORS_ORIGINS` | `http://localhost:3000` | Browser origins |
| `NEXT_PUBLIC_LESSONS_API_URL` | `http://localhost:8001` | API URL for the frontend |
