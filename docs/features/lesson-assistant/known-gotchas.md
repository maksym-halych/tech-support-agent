# Known gotchas: lesson assistant

1. **Keep the write tool lists equal.** `WRITE_TOOLS` is in `agent/src/tech_support_agent/tools/registry.py` and in `frontend/src/components/chat-panel/content.ts`. If they are different, the dashboard does not refresh after a write.
2. **The assistant ID must be the graph name.** `NEXT_PUBLIC_ASSISTANT_ID` must be equal to the graph key in `agent/langgraph.json` (`agent`).
3. **OpenAI and other providers use different APIs.** The agent uses the Responses API only for `https://api.openai.com/v1`. Other providers, for example OpenRouter, use chat completions.
4. **The model name depends on the provider.** OpenAI uses `gpt-6.1-sol`. OpenRouter uses `openai/gpt-6.1-sol`.
5. **Reads retry one time; writes never retry.** A read retries only after a network error. Do not add retries to writes. A repeated write after an uncertain result can change data two times.
6. **The token lives approximately 60 seconds.** A long run can get 401 from the lessons API. The agent then asks the user to send the message again.
7. **The approval description reads the lesson before the run stops.** If the read fails, the description uses the lesson from the conversation history. If that is not available, the card shows only the lesson ID.
8. **The role filter only hides tools.** The lessons API still applies the roles. Do not remove the API checks.
9. **The tool call limit counts each run.** After 10 calls, the next calls do not run. The model gets a message that tells it to answer.
10. **Never put the token in the state or in a message.** Read it only from `signed_in_user(runtime.server_info)`.
11. **Send one decision for each action request.** The model can propose more than one write in one turn. The approval card shows one row for each write.
