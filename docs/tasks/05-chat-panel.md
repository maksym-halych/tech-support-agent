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

## Implementation notes

- Files: `components/chat-panel/ChatPanel.tsx` (launcher button, panel, composer, empty state with starter prompts), `MessageList.tsx`, `ApprovalCard.tsx`, `content.ts` (labels, `WRITE_TOOLS`, `successfulWriteIds`), `use-refresh-after-writes.ts`, `use-chat-open.ts`; `providers/Stream.tsx` (`sendMessage`, `resume`, `newChat`); `lib/agent.ts` (env config and the `HITLRequest` types).
- The panel is a non-modal Radix Dialog: Escape and the close button close it, clicks on the dashboard do not, and focus moves to the message box when it opens. A focus trap would have made the dashboard unusable while the panel is open, so there is none. On screens of 768 px and wider the dashboard leaves 420 px of room for the panel.
- `MessageList` is a small new component instead of the Agent Chat UI's `messages/ai.tsx` and `human.tsx`: those depended on branching, the artifact panel and the generic interrupt view, all removed. It reuses the markdown renderer (moved to `components/markdown/`). Tool calls show as short activity labels ("Searching open lessons"); tool results stay hidden.
- The approval card sends one decision per action request: Approve on a single action sends at once; Reject asks for an optional reason first. The composer is disabled while an approval is pending; to change the proposal, the user rejects it with a reason.
- Removed: `/chat`, `components/thread/` (thread view, history, artifact and state panels, generic interrupt view), `providers/Thread.tsx`, the API URL / assistant ID setup form, file upload and multimodal helpers.
- Fix found here: the Agent Server auth handler replaced an empty metadata dict instead of changing it in place, so threads created without metadata (as `useStream` creates them) lost their owner and their creator got 404. `_stamp_owner` now mutates the server's dict; a regression test covers it.
- Verified: `pnpm lint` (no errors), `pnpm format:check`, `pnpm build`. End to end without a browser: a Node script making the panel's SDK calls (`runs.stream` with `context.timezone`, then `command: {resume: {decisions: [{type: "approve"}]}}`) against `langgraph dev`, the lessons API and a scripted OpenAI-compatible model server: the run paused with the expected `HITLRequest`, nothing was written before approval, and after approval the lesson existed in the API and the agent answered. The teacher's model calls saw only teacher tools and a prompt with role, timezone and local time.
- Not verified here: clicking through the panel in a signed-in browser (the browser pane cannot sign in to Clerk). Check after signing in: open and close, a student booking with approval and the dashboard updating, a rejection leaving the dashboard unchanged, a teacher creating and cancelling, the full-screen panel on a phone, and the console.
