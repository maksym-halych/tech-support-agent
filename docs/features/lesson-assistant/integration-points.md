# Integration points: lesson assistant

## Other features

- [sign-in-and-roles](../sign-in-and-roles/FEATURE.md): Agent Server verifies the token and keeps threads separate per user. The role selects the tools.
- [lesson-scheduling](../lesson-scheduling/FEATURE.md): each tool calls a lessons API endpoint. After a write, the dashboard loads the lessons again.

## External services

| Service | Use | Configuration (`agent/.env`) |
|---|---|---|
| Model provider | Chat model with tool calls | `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `AGENT_MODEL` |
| Lessons API | Reads and writes lessons | `LESSONS_API_URL` |
| Clerk | Token verification | `CLERK_ISSUER`, `CLERK_AUTHORIZED_PARTIES` |
| LangSmith | Optional tracing | `LANGSMITH_TRACING`, `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT` |

## Frontend configuration (`frontend/.env`)

| Variable | Default | Use |
|---|---|---|
| `NEXT_PUBLIC_API_URL` | `http://localhost:2024` | Agent Server URL |
| `NEXT_PUBLIC_ASSISTANT_ID` | `agent` | Graph name from `agent/langgraph.json` |
