# End-to-end flow: sign-in and roles

## Flow 1: sign in and call the lessons API

1. The user opens the frontend. `frontend/src/middleware.ts` sends a user without a session to `/sign-in`.
2. The user signs in with Clerk.
3. Clerk issues a session token with the claims `sub`, `role`, `name` and `email`.
4. The frontend gets a token for each request (`getToken`) and sends it as `Authorization: Bearer`.
5. The lessons API verifies the RS256 signature with the JWKS of the issuer.
6. The lessons API checks `iss`, `exp`, `iat` and `sub`. If authorized parties are set, it also checks `azp`.
7. The lessons API makes a `Caller` from the claims. It records the caller in the `users` table.
8. The endpoint applies the lesson rules for the role of the caller.

## Flow 2: call Agent Server

1. The LangGraph client in the frontend adds a token to each request (`frontend/src/providers/client.ts`).
2. For a request that starts a run, the client asks Clerk for a new token (`skipCache`).
3. Agent Server calls `authenticate` in `agent/src/tech_support_agent/auth.py`. It verifies the token with the same rules as the lessons API.
4. Agent Server attaches the user to the run: ID, name, email, role and the raw token.
5. When a thread or a run starts, Agent Server writes the `owner` into its metadata.
6. Each thread read, update, delete and search is filtered by `owner`.
7. Agent Server refuses each other action, for example a change to an assistant, with 403.

## Flow 3: set a role

1. The administrator runs `make teacher EMAIL=user@example.com` or `make student EMAIL=...`.
2. The `lessons-api-role` command reads `CLERK_SECRET_KEY` from `frontend/.env`.
3. The command finds the user. It sets the public metadata to `{"role": "teacher"}` or `{"role": "student"}` with the Clerk Backend API.
4. The user signs out and signs in again. The new token has the new role.

To list all users and their roles, run `make users`.

## Errors

| Condition | Lessons API | Agent Server |
|---|---|---|
| No token, or a scheme that is not `Bearer` | 401 | 401 |
| Bad signature, issuer, lifetime or `azp` | 401 | 401 |
| Clerk public keys are not available | 503 | 503 |
| Action without an auth rule (Agent Server) | — | 403 |
