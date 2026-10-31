# End-to-end flow: lesson assistant

## Flow 1: a student books a lesson in the chat

1. The user clicks the assistant button. The chat panel opens, and the URL gets `?chat=1`.
2. The user types "Book me a maths lesson tomorrow evening".
3. `StreamProvider.sendMessage` starts a run. It sends the message and the run context `{timezone}`.
4. The client gets a new session token and sends `POST /threads/{id}/runs/stream` to Agent Server.
5. Agent Server verifies the token and the thread owner.
6. `lesson_prompt` adds the system prompt with the name, role, timezone and local time of the user.
7. `RoleToolsMiddleware` removes the tools that the role cannot use.
8. The model calls read tools, for example `find_available_lessons`. `LessonsClient` calls the lessons API with the user token.
9. The agent shows the open lessons in local time and asks the user to select one.
10. The user selects a lesson. The model calls `book_lesson(lesson_id)`.
11. `LessonApprovalMiddleware` reads the lesson and writes a one-line description. The run stops with an approval request.
12. The chat panel shows the approval card.
13. The user clicks **Approve**. The frontend resumes the run with `{"decisions": [{"type": "approve"}]}`.
14. The tool calls `POST /lessons/{lesson_id}/book`. The result goes back to the model.
15. The agent confirms the booking.
16. The run ends. `useRefreshAfterWrites` sees a successful write and loads the dashboard lessons again.

## Flow 2: the user rejects an action

1. The user clicks **Reject** and can type a reason.
2. The frontend resumes the run with `{"type": "reject", "message": "<reason>"}`.
3. The tool does not run. The model gets the reason and asks what to change.

## Flow 3: a teacher opens a lesson in the chat

1. The teacher types "Open a lesson on Friday at 10:00".
2. The agent asks for missing details, for example the subject.
3. The model calls `create_lesson`. The approval card shows the subject, day, time and duration.
4. After approval, the tool calls `POST /lessons`.

## Errors

| Condition | Result for the user |
|---|---|
| The lessons API refuses the action (403, 404, 409, 422) | The agent explains the API message. |
| The session token expired during the run (401) | The agent asks the user to send the message again. |
| The lessons API is not reachable | The agent says that the service is not available. A read is tried one more time first. |
| The turn reached 10 tool calls | The agent answers with the data it has. |
