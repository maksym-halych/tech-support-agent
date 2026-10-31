# Per-app responsibility: lesson assistant

| App | Responsibility |
|---|---|
| `frontend/` | Shows the chat panel and the approval card. Sends messages, the run context and decisions. Loads the dashboard again after a write. |
| `agent/` | Runs the model. Selects tools for the role. Stops before each write for approval. Calls the lessons API as the user. Keeps the token away from the model. |
| `api/` | Applies all lesson rules to agent calls, the same as to dashboard calls. |
| Model provider | Any OpenAI-compatible provider, for example OpenAI or OpenRouter. |
