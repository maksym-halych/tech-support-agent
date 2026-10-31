# Task 7 — FAQ for the assistant

## Goal

The assistant answers the questions that users usually ask support (how to book, why a booking failed, how to become a teacher) from one maintained FAQ, instead of from what the model guesses about the app.

## Scope

- `agent/src/tech_support_agent/faq/faq.md`: the FAQ. Each `## ` heading is a question, followed by an `Audience:` line (`everyone`, `student` or `teacher`), a `Keywords:` line with other words users say, and the answer. Answers use the dashboard's labels in bold and the API's error messages as they are.
- `faq/search.py`: loads the file once and scores entries by shared terms (question ×3, keywords ×2, answer ×1) after stopwords and a light stemming. A role sees `everyone` entries and its own; nobody signed in sees all.
- `search_faq` tool: a read tool for every role and for signed-out users. It returns the three best entries, or a note that nothing matched.
- The system prompt makes the assistant tech support first: for how-to and "why" questions it calls `search_faq`, answers with the buttons to click, then offers to do the action. It must not describe screens or rules that the FAQ did not return.
- Chat panel: a how-to starter for each role, and an empty-state line that says the assistant answers questions about the app.

## Out of scope

Embeddings or a vector store: the FAQ is about 30 entries, and term search finds the right one for the questions in the tests. Removing the write tools: the assistant still acts for the user after approval.

## Acceptance

- `search_faq` returns the matching entry first for a how-to question, an API error message and a role question, and never returns another role's entries.
- `make test` and `make lint` pass; `pnpm -C frontend lint`, `format:check` and `build` pass.

## Implementation notes

- Add a question by adding a `## ` section to `faq.md`; no code change is needed. Add the words users say to `Keywords:`, because the search matches words, not meaning. The model rephrases the user's question in English before it searches, so the FAQ stays in English.
- `agent/tests/test_faq.py` checks that every entry parses and that sample questions find their entry. Add a test when you add an entry that users ask in very different words.
