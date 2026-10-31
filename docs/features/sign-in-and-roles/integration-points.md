# Integration points: sign-in and roles

## Other features

- [lesson-scheduling](../lesson-scheduling/FEATURE.md): each endpoint gets the caller from `current_caller`. The role decides the allowed actions.
- [lesson-assistant](../lesson-assistant/FEATURE.md): the run user gives the token to `LessonsClient`. The role selects the tools the model sees and the actions that need approval.

## External services

| Service | Use | Configuration |
|---|---|---|
| Clerk frontend API | Sign-in; JWKS at `{issuer}/.well-known/jwks.json` | `LESSONS_API_CLERK_ISSUER` in `api/.env`, `CLERK_ISSUER` in `agent/.env` |
| Clerk Backend API | Role command, at `https://api.clerk.com/v1` | `CLERK_SECRET_KEY` in `frontend/.env` |
| Clerk dashboard | Session token template (see `data-model.md`) | Set one time for each Clerk instance |

The issuer in `api/.env` and `agent/.env` and the Clerk keys in `frontend/.env` must be from the same Clerk instance.
