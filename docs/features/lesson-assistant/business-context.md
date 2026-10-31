# Business context: lesson assistant

## Problem

Users write to support to book, move or cancel lessons. A person must ask for details and do the change by hand. The assistant does this work in the product. A person stays in control of each change.

## Users

- A **student** asks the agent to find, book or cancel lessons.
- A **teacher** asks the agent to open or cancel lessons.

## Rules

1. The agent acts as the signed-in user. The lessons API applies the same rules as for the dashboard.
2. Each booking, new lesson and cancellation stops for the approval of the user.
3. The approval card says in plain words what will change: subject, teacher, local day and time, duration.
4. The model sees only the tools of the role of the user.
5. The agent uses only lessons and IDs that tools returned. It does not invent them.
6. If a date, time or subject is not clear, the agent asks a short question.
7. The agent uses the timezone of the user for each date and time.
8. After a rejection, the agent asks what to change.
9. The agent makes 10 tool calls or fewer in one turn.
10. The agent does not repeat a write automatically.
11. The agent talks only about lessons. It answers in the language of the user.
12. The model never sees the session token.

## Reasons

- Approval before each write keeps a person in control of real data.
- The same API rules for the agent and the dashboard prevent actions that the user cannot do.
- No automatic write retry prevents double bookings after an uncertain result.
