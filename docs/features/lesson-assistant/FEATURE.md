# Lesson assistant

## Summary

A chat panel on the lessons dashboard connects the user to an AI agent. The agent understands the request, reads lessons, and proposes a booking, a new lesson or a cancellation. The user approves or rejects each change. Then the agent does the change as the user through the lessons API.

## Scope

- The chat panel and the approval card in the frontend.
- The agent on LangGraph Agent Server: prompt, tools and middleware.
- The lessons API client of the agent.

The lesson rules are not in scope. See [lesson-scheduling](../lesson-scheduling/FEATURE.md). Token verification is not in scope. See [sign-in-and-roles](../sign-in-and-roles/FEATURE.md).

## Glossary

| Term | Meaning |
|---|---|
| Thread | One conversation. It belongs to one user. |
| Run | One turn of the agent in a thread. A user message or a decision starts it. |
| Run context | Data that the frontend sends with each run: the timezone of the browser. |
| Read tool | A tool that only reads lessons. It does not need approval. |
| Write tool | `book_lesson`, `create_lesson` or `cancel_lesson`. Each call needs approval. |
| Approval request | The interrupt that stops a run before a write tool (`HITLRequest`). |
| Approval card | The frontend card that shows an approval request with **Approve** and **Reject**. |
| Decision | The answer of the user: `approve`, or `reject` with an optional reason. |

## Status

Implemented. Tests: `agent/tests/test_graph.py`, `agent/tests/test_approval.py`, `agent/tests/test_lesson_tools.py`, `agent/tests/test_lessons_client.py`, `agent/tests/test_middleware.py`.
