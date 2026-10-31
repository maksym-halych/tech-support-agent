# Feature documentation

This folder describes each feature from end to end. Read the feature docs before you change a feature. AI agents read them to get the business context.

## Features

| Feature | What it does |
|---|---|
| [sign-in-and-roles](sign-in-and-roles/FEATURE.md) | Clerk signs in the user. The session token gives the role: student or teacher. |
| [lesson-scheduling](lesson-scheduling/FEATURE.md) | Teachers open lessons. Students book and cancel lessons on the dashboard. |
| [lesson-assistant](lesson-assistant/FEATURE.md) | A chat agent does the same lesson actions. The user approves each change. |

## Files in each feature folder

| File | Content |
|---|---|
| `FEATURE.md` | Summary, scope and glossary |
| `business-context.md` | Users, business rules and the reasons for them |
| `end-to-end-flow.md` | Each flow, step by step |
| `entry-points.json` | Entry points in the code, and the tests |
| `per-app-responsibility.md` | What each app does in the feature |
| `backend-structure.md` | Backend files and their content |
| `frontend-structure.md` | Frontend files and their content |
| `data-model.md` | Data that the feature keeps or sends |
| `integration-points.md` | Links to other features and external services |
| `known-gotchas.md` | Known traps, and how to prevent problems |

## Writing rules

- Write in ASD-STE100 Simplified Technical English.
- Keep sentences short: 20 words or fewer for instructions, 25 words or fewer for descriptions.
- Use the active voice and the present tense.
- Use one term for one thing. Use the terms from the glossary in `FEATURE.md`.
- Describe the code as it is now. Do not describe plans.
- Give file paths from the repository root.

## Keep the docs in sync with the code

After each code change, do these steps:

1. Find the features that the change touches. Use `entry-points.json` of each feature.
2. Compare the change with the docs of these features.
3. If the docs are not correct, ask the engineer: "Update the feature docs?"
4. If the engineer agrees, update the docs in the same commit as the code.
5. If the change adds a feature, make a new folder with the ten files.

The product owner reviews `business-context.md` and `end-to-end-flow.md`. If the flow is not correct, change the code first. Then update the docs.
