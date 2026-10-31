# Frontend structure: sign-in and roles

| File | Content |
|---|---|
| `frontend/src/middleware.ts` | `clerkMiddleware`: protects all routes except `/sign-in` |
| `frontend/src/app/layout.tsx` | `ClerkProvider` around the app |
| `frontend/src/app/sign-in/[[...sign-in]]/page.tsx` | Clerk sign-in page |
| `frontend/src/lib/clerkAppearance.ts`, `frontend/src/lib/clerkLocale.ts` | Clerk look and language |
| `frontend/src/providers/client.ts` | `createClient`: adds a token to each Agent Server request; a new token when a run starts |
| `frontend/src/lib/lessons-api.ts` | `createLessonsApi`: adds a token to each lessons API request |
| `frontend/src/components/lessons/Dashboard.tsx` | `Header`: shows the name and the role badge |
