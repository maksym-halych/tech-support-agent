# Business context: sign-in and roles

## Users

- A **student** books and cancels lessons.
- A **teacher** opens and cancels lessons.
- An **administrator** sets the role of a user with `make teacher` or `make student`.

## Rules

1. Each request to the lessons API and to Agent Server must have a valid session token.
2. The role is `teacher` only if the `role` claim is exactly `teacher`. Each other value, and no value, gives `student`.
3. A new user is a student. Only an administrator can make a user a teacher.
4. A role change applies after the user signs in again. The old session token keeps the old role.
5. The agent acts as the signed-in user. It cannot do more than the user can do on the dashboard.
6. A user can see and use only their own chat threads.

## Reasons

- One identity for the dashboard and the agent. The lessons API applies the same rules to both.
- The role lives in Clerk. The lessons API copies it into the `users` table only to list teachers.
- A mistake in the role claim gives `student`, the role with fewer permissions.
