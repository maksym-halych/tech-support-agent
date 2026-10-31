# Backend structure: lesson assistant

| File | Content |
|---|---|
| `agent/langgraph.json` | Graph `agent` → `graph.py:build_agent`; auth handler; `.env` |
| `agent/src/tech_support_agent/graph.py` | `create_lessons_agent` (one `create_agent` with tools and middleware); `create_chat_model` |
| `agent/src/tech_support_agent/prompts.py` | `SYSTEM_PROMPT`, `run_details`, `MAX_TOOL_CALLS_PER_TURN` |
| `agent/src/tech_support_agent/context.py` | `AgentContext` (run context), `SignedInUser`, `signed_in_user()` |
| `agent/src/tech_support_agent/config.py` | `Settings`: model, key, base URL, Clerk issuer, lessons API URL |
| `agent/src/tech_support_agent/middleware/prompt.py` | `lesson_prompt`: the system prompt with the user and the local time |
| `agent/src/tech_support_agent/middleware/roles.py` | `RoleToolsMiddleware`: removes tools that the role cannot use |
| `agent/src/tech_support_agent/middleware/limits.py` | `tool_call_limit`: 10 tool calls or fewer in one run |
| `agent/src/tech_support_agent/middleware/approval.py` | `LessonApprovalMiddleware`: approval requests with plain descriptions |
| `agent/src/tech_support_agent/tools/registry.py` | `TOOLS`, `WRITE_TOOLS`, `tool_names_for(role)` |
| `agent/src/tech_support_agent/tools/shared.py` | `lesson_tool` decorator, user and timezone helpers, JSON output |
| `agent/src/tech_support_agent/tools/*.py` | One file for each tool |
| `agent/src/tech_support_agent/lessons/client.py` | `LessonsClient`, `client_for(user)`, `LessonsApiError` |
| `agent/src/tech_support_agent/lessons/models.py` | `Lesson`, `Teacher`, `LessonView` |

## Middleware order

`lesson_prompt` → `RoleToolsMiddleware` → `tool_call_limit` → `LessonApprovalMiddleware`

## Tools

| Tool | Role | API call | Approval |
|---|---|---|---|
| `get_current_datetime` | any | — | no |
| `list_my_lessons` | student, teacher | `GET /lessons/mine` | no |
| `list_teachers` | student | `GET /teachers` | no |
| `find_available_lessons` | student | `GET /lessons/available` | no |
| `book_lesson` | student | `POST /lessons/{id}/book` | yes |
| `create_lesson` | teacher | `POST /lessons` | yes |
| `cancel_lesson` | student, teacher | `POST /lessons/{id}/cancel` | yes |
