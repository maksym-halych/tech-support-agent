# Frontend structure: lesson assistant

| File | Content |
|---|---|
| `frontend/src/app/page.tsx` | Puts `StreamProvider` around the dashboard and the chat panel |
| `frontend/src/components/chat-panel/ChatPanel.tsx` | The panel, the empty state with starter prompts, the message input |
| `frontend/src/components/chat-panel/MessageList.tsx` | User and assistant messages, tool activity, typing indicator |
| `frontend/src/components/chat-panel/ApprovalCard.tsx` | Shows each action request; **Approve**, **Reject** with an optional reason |
| `frontend/src/components/chat-panel/content.ts` | `WRITE_TOOLS`, action titles, `successfulWriteIds` |
| `frontend/src/components/chat-panel/use-chat-open.ts` | Open or closed state in the URL (`?chat=1`) |
| `frontend/src/components/chat-panel/use-refresh-after-writes.ts` | Loads the dashboard lessons again after a successful write |
| `frontend/src/providers/Stream.tsx` | `StreamProvider`: `sendMessage`, `resume`, `newChat`; the thread ID in the URL |
| `frontend/src/providers/client.ts` | LangGraph client with a token on each request |
| `frontend/src/lib/agent.ts` | Agent URL, assistant ID, `HITLRequest`, `Decision` types |
| `frontend/src/app/api/[..._path]/route.ts` | Optional proxy to Agent Server; not used when `NEXT_PUBLIC_API_URL` points to Agent Server |

Starter prompts: student — "Find me a lesson tomorrow", "Cancel my next lesson"; teacher — "Open a lesson on Friday at 10:00", "Cancel my next lesson".
