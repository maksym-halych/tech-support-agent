"use client";

import type { Message } from "@langchain/langgraph-sdk";
import { LoaderCircle } from "lucide-react";
import { MarkdownText } from "@/components/markdown/markdown-text";
import { getContentString, toolActivity } from "./content";

function HumanBubble({ message }: { message: Message }) {
  return (
    <p className="ml-auto w-fit max-w-[85%] whitespace-pre-wrap rounded-2xl bg-muted px-3 py-2 text-sm">
      {getContentString(message.content)}
    </p>
  );
}

function ToolActivity({
  names,
  running,
}: {
  names: string[];
  running: boolean;
}) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {names.map((name, index) => (
        <span
          key={`${name}-${index}`}
          className="inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs text-muted-foreground"
        >
          {running && <LoaderCircle className="size-3 animate-spin" />}
          {toolActivity(name)}
        </span>
      ))}
    </div>
  );
}

function AssistantMessage({
  message,
  running,
}: {
  message: Message;
  running: boolean;
}) {
  const text = getContentString(message.content);
  const toolNames =
    message.type === "ai"
      ? (message.tool_calls ?? []).map((call) => call.name)
      : [];
  return (
    <div className="mr-auto flex w-full flex-col gap-2 text-sm">
      {text && <MarkdownText>{text}</MarkdownText>}
      {toolNames.length > 0 && (
        <ToolActivity
          names={toolNames}
          running={running}
        />
      )}
    </div>
  );
}

export function TypingIndicator() {
  return (
    <div
      className="mr-auto flex h-8 items-center gap-1 rounded-2xl bg-muted px-4"
      aria-label="Assistant is typing"
    >
      {[0, 0.5, 1].map((delay) => (
        <span
          key={delay}
          className="size-1.5 animate-[pulse_1.5s_ease-in-out_infinite] rounded-full bg-foreground/50"
          style={{ animationDelay: `${delay}s` }}
        />
      ))}
    </div>
  );
}

/**
 * The conversation: user bubbles, assistant replies as markdown, and tool calls
 * as short activity labels. Tool results stay hidden; the assistant explains them.
 */
export function MessageList({
  messages,
  isLoading,
}: {
  messages: Message[];
  isLoading: boolean;
}) {
  const last = messages[messages.length - 1];
  return (
    <>
      {messages.map((message, index) => {
        if (message.type === "human") {
          return (
            <HumanBubble
              key={message.id ?? index}
              message={message}
            />
          );
        }
        if (message.type !== "ai") return null;
        return (
          <AssistantMessage
            key={message.id ?? index}
            message={message}
            running={isLoading && message === last}
          />
        );
      })}
      {isLoading && last?.type !== "ai" && <TypingIndicator />}
    </>
  );
}
