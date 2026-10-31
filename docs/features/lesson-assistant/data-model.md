# Data model: lesson assistant

## Run context (`AgentContext`)

| Field | Rule |
|---|---|
| `timezone` | IANA timezone name, for example `Europe/Kyiv`. Default: `Europe/Kyiv`. Unknown names are refused. |

## Signed-in user (`SignedInUser`)

`id`, `name`, `role`, `token`. Agent Server gives it for each run. The `token` is a `SecretStr`. It is not in the graph state, the messages or the logs.

## Graph state

`messages`: the conversation. The checkpointer of Agent Server keeps it for each thread.

## Tool output (`LessonView`)

| Field | Content |
|---|---|
| `id`, `subject`, `status` | From the API |
| `teacher`, `student` | Names |
| `day`, `time`, `starts_at_local` | Local to the user timezone |
| `starts_at_utc` | UTC, for exact values |
| `duration_minutes` | From the API |

## Approval request (`HITLRequest`)

```json
{
  "action_requests": [{"name": "book_lesson", "args": {"lesson_id": "..."}, "description": "Book Maths with Anna on ..."}],
  "review_configs": [{"action_name": "book_lesson", "allowed_decisions": ["approve", "reject"]}]
}
```

## Decisions (resume value)

```json
{"decisions": [{"type": "approve"}]}
{"decisions": [{"type": "reject", "message": "Too late, find an earlier slot"}]}
```

Send one decision for each action request, in the same order.

## Thread metadata

`owner`: the Clerk user ID. See [sign-in-and-roles](../sign-in-and-roles/data-model.md).
