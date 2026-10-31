# Backend structure: sign-in and roles

## Lessons API (`api/`)

| File | Content |
|---|---|
| `api/src/lessons_api/auth.py` | `TokenVerifier`, `jwks_key_resolver`, `role_from_claims`, `verified_caller` |
| `api/src/lessons_api/app.py` | `current_caller`: verifies and records the caller for each endpoint |
| `api/src/lessons_api/roles.py` | `lessons-api-role` command: lists Clerk users and sets a role |
| `api/src/lessons_api/config.py` | `LESSONS_API_CLERK_ISSUER`, `LESSONS_API_CLERK_AUTHORIZED_PARTIES`, `LESSONS_API_CORS_ORIGINS` |

## Agent (`agent/`)

| File | Content |
|---|---|
| `agent/langgraph.json` | `auth.path` points Agent Server to `auth.py:auth` |
| `agent/src/tech_support_agent/auth.py` | Auth handlers: authenticate, thread and run ownership, store namespace, refusal of all other actions |
| `agent/src/tech_support_agent/clerk.py` | `TokenVerifier` for Agent Server, with the same rules as the lessons API |
| `agent/src/tech_support_agent/context.py` | `SignedInUser` and `signed_in_user()`: read the user and the token from the run |
| `agent/src/tech_support_agent/config.py` | `CLERK_ISSUER`, `CLERK_AUTHORIZED_PARTIES` |
