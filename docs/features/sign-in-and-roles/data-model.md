# Data model: sign-in and roles

## Session token claims

| Claim | Use |
|---|---|
| `sub` | User ID. It becomes `Caller.id` and the thread owner. |
| `role` | `teacher` gives the teacher role. Each other value gives `student`. |
| `name` | Display name. If it is empty, the API uses `email`, then `Unnamed user`. |
| `email` | Email of the user |
| `iss` | Must be equal to the issuer |
| `azp` | Must be in the authorized parties, if the list is not empty |
| `exp`, `iat` | Necessary. The verifier accepts 5 seconds of clock difference. |

The Clerk session token template adds the custom claims:

```json
{"role": "{{user.public_metadata.role}}", "name": "{{user.full_name}}", "email": "{{user.primary_email_address}}"}
```

## `Caller` (`api/src/lessons_api/models.py`)

`id`, `name`, `email`, `role`. The lessons API makes it from the claims for each request.

## `users` table (SQLite, `api/src/lessons_api/storage.py`)

| Column | Type | Note |
|---|---|---|
| `id` | TEXT, primary key | Clerk user ID |
| `name` | TEXT | From the last token |
| `email` | TEXT | From the last token |
| `role` | TEXT | `student` or `teacher` |
| `last_seen_at` | TEXT | UTC time of the last request |

The API updates the row on each request.

## Agent Server user (`agent/src/tech_support_agent/auth.py`)

`identity`, `display_name`, `email`, `role`, `access_token`, `permissions` (empty). `signed_in_user()` reads it as `SignedInUser`: `id`, `name`, `role`, `token`. The `token` field is a `SecretStr` and is excluded from serialization.

## Thread and run metadata

`owner`: the Clerk user ID of the user who made the thread or the run.
