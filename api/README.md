# lessons-api

FastAPI service for teachers, lesson slots and bookings. See the repository README for how it fits with `agent/` and `frontend/`.

```bash
uv run lessons-api                      # serve on 127.0.0.1:8001, Swagger at /docs
uv run lessons-api-export-openapi --output openapi.json
uv run pytest
```

Settings come from `LESSONS_API_*` environment variables or `api/.env` (see `.env.example`).
