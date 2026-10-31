"use client";

import { useAuth } from "@clerk/nextjs";
import { type Message } from "@langchain/langgraph-sdk";
import { useStream } from "@langchain/langgraph-sdk/react";
import { useQueryState } from "nuqs";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  type ReactNode,
} from "react";
import {
  AGENT_URL,
  ASSISTANT_ID,
  AUTH_SCHEME,
  type Decision,
  type HITLRequest,
  type StateType,
} from "@/lib/agent";
import { createClient } from "./client";

type RunContext = { timezone: string };

const useTypedStream = useStream<
  StateType,
  {
    InterruptType: HITLRequest;
    ConfigurableType: RunContext;
    UpdateType: { messages?: Message[] | Message | string };
  }
>;

type Stream = ReturnType<typeof useTypedStream>;

interface StreamContextValue {
  stream: Stream;
  threadId: string | null;
  /** Send a user message, with the browser's timezone as run context. */
  sendMessage: (text: string) => void;
  /** Answer the pending approval request, one decision per action. */
  resume: (decisions: Decision[]) => void;
  /** Start a new, empty conversation. */
  newChat: () => void;
}

const StreamContext = createContext<StreamContextValue | undefined>(undefined);

function runContext(): RunContext {
  return { timezone: Intl.DateTimeFormat().resolvedOptions().timeZone };
}

const STREAM_OPTIONS = {
  streamMode: ["values" as const],
  streamResumable: true,
};

export function StreamProvider({ children }: { children: ReactNode }) {
  const [threadId, setThreadId] = useQueryState("threadId");
  const { getToken } = useAuth();
  // Clerk returns a new `getToken` on most renders. Read it through a ref so
  // the client, and with it the open stream, survives session refreshes.
  const getTokenRef = useRef(getToken);
  useEffect(() => {
    getTokenRef.current = getToken;
  }, [getToken]);

  const tokenFor = useCallback(
    (options?: { skipCache?: boolean }) => getTokenRef.current(options),
    [],
  );
  const client = useMemo(
    // The client only calls `tokenFor` per request, never during render.
    // eslint-disable-next-line react-hooks/refs
    () => createClient(AGENT_URL, undefined, AUTH_SCHEME, tokenFor),
    [tokenFor],
  );

  const stream = useTypedStream({
    client,
    assistantId: ASSISTANT_ID,
    threadId: threadId ?? null,
    fetchStateHistory: false,
    onThreadId: (id) => void setThreadId(id),
  });

  const sendMessage = useCallback(
    (text: string) => {
      const message: Message = {
        id: crypto.randomUUID(),
        type: "human",
        content: text,
      };
      stream.submit(
        { messages: [message] },
        {
          ...STREAM_OPTIONS,
          context: runContext(),
          optimisticValues: (prev) => ({
            ...prev,
            messages: [...(prev.messages ?? []), message],
          }),
        },
      );
    },
    [stream],
  );

  const resume = useCallback(
    (decisions: Decision[]) => {
      stream.submit(undefined, {
        ...STREAM_OPTIONS,
        context: runContext(),
        command: { resume: { decisions } },
      });
    },
    [stream],
  );

  const newChat = useCallback(() => void setThreadId(null), [setThreadId]);

  const value = useMemo(
    () => ({ stream, threadId, sendMessage, resume, newChat }),
    [stream, threadId, sendMessage, resume, newChat],
  );

  return (
    <StreamContext.Provider value={value}>{children}</StreamContext.Provider>
  );
}

export function useStreamContext(): StreamContextValue {
  const context = useContext(StreamContext);
  if (context === undefined) {
    throw new Error("useStreamContext must be used within a StreamProvider");
  }
  return context;
}
