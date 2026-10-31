# Known gotchas: sign-in and roles

1. **Tokens expire after approximately 60 seconds.** Get a token for each request. Do not keep a token in a static header.
2. **A run uses the token of the request that started it.** The client gets a new token for each run. If a run is longer than the token lifetime, the lessons API returns 401. The agent then asks the user to send the message again.
3. **A role change needs a new sign-in.** The old token keeps the old role until it expires.
4. **No session token template means no role claim.** Then each user is a student. Make sure that the template is set in the Clerk dashboard.
5. **`GET /teachers` shows only teachers who signed in one time or more.** The list comes from the `users` table.
6. **Change thread metadata in place.** `_stamp_owner` changes the dict that Agent Server holds. If you replace the dict, Agent Server loses the owner.
7. **JWKS fetch blocks.** `PyJWKClient` uses blocking I/O when the key is not in the cache. Agent Server calls it with `asyncio.to_thread`.
8. **An empty list of authorized parties skips the `azp` check.** Set the list for each deployment.
9. **The role command reads `CLERK_SECRET_KEY` from `../frontend/.env`.** The path is relative to `api/`. Run it with `make`, from the repository root.
