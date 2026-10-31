# Per-app responsibility: sign-in and roles

| App | Responsibility |
|---|---|
| Clerk | Signs in users. Issues session tokens. Keeps the role in the public metadata. |
| `frontend/` | Protects all pages except `/sign-in`. Gets a token for each request. Shows the role badge. |
| `api/` | Verifies tokens. Makes a `Caller` from the claims. Records users. Applies the role on each endpoint. Contains the role command. |
| `agent/` | Verifies tokens in Agent Server. Keeps threads separate per user. Gives the token to the lessons API client, not to the model. |
