# Per-app responsibility: lesson scheduling

| App | Responsibility |
|---|---|
| `api/` | Owns all lesson rules and the storage. Returns clear error messages. |
| `frontend/` | Shows the lessons. Sends the actions of the user to the API. Shows the API result or error. It does not repeat the rules. |
| `agent/` | Calls the same endpoints as the signed-in user. See [lesson-assistant](../lesson-assistant/FEATURE.md). |
