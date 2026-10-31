# Sign-in and roles

## Summary

Clerk signs in each user and issues a session token (a JWT). The token has a `role` claim. The lessons API and Agent Server verify the token on each request. The role decides what the user can do.

## Scope

- Sign-in page and page protection in the frontend.
- Token verification in the lessons API and in Agent Server.
- The role rule: only the exact value `teacher` gives the teacher role.
- The command that sets the role of a user in Clerk.
- Chat thread ownership in Agent Server.

Lesson rules are not in scope. See [lesson-scheduling](../lesson-scheduling/FEATURE.md).

## Glossary

| Term | Meaning |
|---|---|
| Session token | The Clerk JWT that the frontend sends as `Authorization: Bearer`. It is valid for approximately 60 seconds. |
| Caller | The signed-in user that sends a request. |
| Role | `student` or `teacher`. It comes from the `role` claim. |
| Issuer | The Clerk frontend API URL. It signs the tokens and publishes the public keys (JWKS). |
| Public metadata | Clerk user data. The session token template copies `role` from it into the token. |
| Owner | The user ID that Agent Server writes into the metadata of a thread or run. |

## Status

Implemented. Tests: `api/tests/test_auth.py`, `api/tests/test_roles.py`, `agent/tests/test_auth.py`.
