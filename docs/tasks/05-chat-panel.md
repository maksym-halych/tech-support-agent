# Task 5 — Right-side chat panel

## Goal

Replace the full-page Agent Chat UI with a compact chat panel that slides in from the right over the lessons dashboard, where users ask the agent to create, book or cancel lessons and approve each action inline.

## Layout

- The dashboard stays the only page. A floating "Ask assistant" button sits bottom-right.
- Clicking it opens a right-side panel (about 420 px wide, full height, on top of the dashboard; full screen below 768 px). Open/closed state is kept in the URL (`?chat=1`) together with the existing `threadId`, so a reload restores the conversation.
- Panel header: title, "New chat" button, close button. Body: message list. Footer: input with send/stop.
- Remove `/chat`, the thread history sidebar, the artifact panel, the state panel and the API URL / assistant ID setup form; the agent URL and assistant ID come only from env.

## Components

- `components/chat-panel/ChatPanel.tsx` — panel shell, opens over the dashboard, focus trap and Escape to close (Radix Dialog or Sheet).
- `components/chat-panel/MessageList.tsx` — reuses the existing `messages/human.tsx` and `messages/ai.tsx` rendering, without tool-call debug output by default.
- `components/chat-panel/ApprovalCard.tsx` — renders the agent's `HITLRequest`: for each action request the `description`, and Approve / Reject buttons (Reject with an optional reason). Submits `stream.submit(undefined, { command: { resume: { decisions } } })`. Disabled while a run is in progress. Replaces `GenericInterruptView` for this interrupt shape.
- `StreamProvider` stays the single LangGraph SDK connection, mounted around the dashboard so the panel keeps its stream while closed. Each submit passes `context: { timezone: Intl.DateTimeFormat().resolvedOptions().timeZone }`.

## Dashboard sync

When a run finishes (`isLoading` goes from true to false) and its messages include a successful `book_lesson`, `create_lesson` or `cancel_lesson` tool result, call `useLessons().refresh()` so the dashboard updates without a reload.

## Starter prompts

When a thread is empty, show two role-specific suggestions as buttons, e.g. student: "Find me a lesson tomorrow", "Cancel my next lesson"; teacher: "Open a lesson on Friday at 10:00", "Cancel my next lesson".

## Verification

- `pnpm lint`, `pnpm format:check` and `pnpm build` pass.
- Browser checks after a human signs in: panel opens and closes; a student books a lesson through chat with an approval card and the dashboard updates; reject leaves the dashboard unchanged; teacher flow creates and cancels a lesson; mobile width shows the panel full screen; no console errors.

## Acceptance criteria

- The dashboard is usable with the panel closed; the chat never replaces the page.
- Every write proposed by the agent shows an approval card, and nothing changes until Approve is clicked.
- The dashboard reflects completed writes without a manual reload.
- README and CLAUDE.md describe the final three-app setup.
