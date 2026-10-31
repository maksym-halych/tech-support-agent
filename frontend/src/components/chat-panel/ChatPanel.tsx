"use client";

import * as Dialog from "@radix-ui/react-dialog";
import {
  AlertCircle,
  ArrowUp,
  MessageSquare,
  Square,
  SquarePen,
  X,
} from "lucide-react";
import { useRef, useState, type FormEvent } from "react";
import { StickToBottom } from "use-stick-to-bottom";
import { AppLogo } from "@/components/icons/logo";
import { Button } from "@/components/ui/button";
import { TextArea } from "@/components/ui/textArea";
import { TooltipIconButton } from "@/components/ui/tooltip-icon-button";
import { useLessons } from "@/hooks/use-lessons";
import type { Role } from "@/lib/lessons-api";
import { AGENT_URL, isHITLRequest } from "@/lib/agent";
import { useStreamContext } from "@/providers/Stream";
import { ApprovalCard } from "./ApprovalCard";
import { MessageList } from "./MessageList";
import { useChatOpen } from "./use-chat-open";
import { useRefreshAfterWrites } from "./use-refresh-after-writes";

const STARTERS: Record<Role, string[]> = {
  student: ["Find me a lesson tomorrow", "Cancel my next lesson"],
  teacher: ["Open a lesson on Friday at 10:00", "Cancel my next lesson"],
};

function errorMessage(error: unknown): string {
  if (error instanceof Error) return error.message;
  return typeof error === "string" ? error : "Something went wrong.";
}

function EmptyState({
  role,
  onPick,
}: {
  role: Role | undefined;
  onPick: (prompt: string) => void;
}) {
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-4 px-6 py-12 text-center">
      <AppLogo className="size-8 text-primary" />
      <div>
        <p className="font-medium">How can I help?</p>
        <p className="text-sm text-muted-foreground">
          {role === "teacher"
            ? "I can open new lesson slots and cancel your lessons."
            : "I can find and book lessons, and cancel your bookings."}
        </p>
      </div>
      {role && (
        <div className="flex flex-wrap justify-center gap-2">
          {STARTERS[role].map((prompt) => (
            <button
              key={prompt}
              type="button"
              onClick={() => onPick(prompt)}
              className="rounded-full border px-3 py-1.5 text-sm text-muted-foreground transition-colors hover:border-primary hover:text-foreground"
            >
              {prompt}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function Composer({
  disabledReason,
  inputRef,
}: {
  disabledReason: string | null;
  inputRef: React.RefObject<HTMLTextAreaElement | null>;
}) {
  const { stream, sendMessage } = useStreamContext();
  const [input, setInput] = useState("");
  const blocked = disabledReason !== null;

  const submit = (event?: FormEvent) => {
    event?.preventDefault();
    const text = input.trim();
    if (!text || stream.isLoading || blocked) return;
    sendMessage(text);
    setInput("");
  };

  return (
    <form
      onSubmit={submit}
      className="border-t p-3"
    >
      {disabledReason && (
        <p className="mb-2 text-xs text-muted-foreground">{disabledReason}</p>
      )}
      <div className="flex items-end gap-2 rounded-2xl bg-muted/60 p-2">
        <TextArea
          ref={inputRef}
          growing
          maxRows={6}
          value={input}
          disabled={blocked}
          onChange={(event) => setInput(event.target.value)}
          onKeyDown={(event) => {
            if (
              event.key === "Enter" &&
              !event.shiftKey &&
              !event.nativeEvent.isComposing
            ) {
              event.preventDefault();
              submit();
            }
          }}
          placeholder="Ask about your lessons…"
          aria-label="Message"
          className="min-h-10 flex-1 resize-none border-none bg-transparent px-2 py-2 text-sm shadow-none outline-none focus-visible:ring-0"
        />
        {stream.isLoading ? (
          <Button
            type="button"
            size="icon"
            variant="secondary"
            aria-label="Stop"
            onClick={() => void stream.stop()}
          >
            <Square className="size-3.5 fill-current" />
          </Button>
        ) : (
          <Button
            type="submit"
            size="icon"
            aria-label="Send"
            disabled={!input.trim() || blocked}
          >
            <ArrowUp />
          </Button>
        )}
      </div>
    </form>
  );
}

function Conversation({ onPick }: { onPick: (prompt: string) => void }) {
  const { stream, resume } = useStreamContext();
  const { me } = useLessons();
  const interrupt = stream.interrupt;
  const request = isHITLRequest(interrupt?.value) ? interrupt.value : null;
  const messages = stream.messages;

  if (messages.length === 0 && !stream.isLoading && !request) {
    return (
      <EmptyState
        role={me?.role}
        onPick={onPick}
      />
    );
  }
  return (
    <StickToBottom
      className="relative flex-1 overflow-hidden"
      resize="smooth"
      initial="instant"
    >
      <StickToBottom.Content
        className="flex flex-col gap-4 p-4"
        scrollClassName="scrollbar"
      >
        <MessageList
          messages={messages}
          isLoading={stream.isLoading}
        />
        {request && (
          <ApprovalCard
            key={interrupt?.id ?? JSON.stringify(request.action_requests)}
            request={request}
            disabled={stream.isLoading}
            onDecide={resume}
          />
        )}
      </StickToBottom.Content>
    </StickToBottom>
  );
}

/**
 * The lesson assistant: a floating button and a right-side panel over the
 * dashboard. The panel is not modal, so the dashboard stays usable and shows
 * changes as soon as the agent makes them.
 */
export function ChatPanel() {
  const [open, setOpen] = useChatOpen();
  const { stream, threadId, sendMessage, newChat } = useStreamContext();
  const { refresh } = useLessons();
  const inputRef = useRef<HTMLTextAreaElement>(null);
  useRefreshAfterWrites(stream.messages, stream.isLoading, refresh);

  const pendingApproval = isHITLRequest(stream.interrupt?.value);
  const disabledReason = pendingApproval
    ? "Approve or reject the action above to continue. To change it, reject it and say what should change."
    : null;

  return (
    <>
      {!open && (
        <Button
          size="lg"
          className="fixed bottom-6 right-6 z-30 shadow-lg"
          onClick={() => void setOpen(true)}
        >
          <MessageSquare />
          Ask assistant
        </Button>
      )}
      <Dialog.Root
        open={open}
        onOpenChange={(next) => void setOpen(next)}
        modal={false}
      >
        <Dialog.Portal>
          <Dialog.Content
            onInteractOutside={(event) => event.preventDefault()}
            onOpenAutoFocus={(event) => {
              event.preventDefault();
              inputRef.current?.focus();
            }}
            className="fixed inset-0 z-40 flex flex-col bg-background shadow-2xl outline-none data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:slide-out-to-right data-[state=open]:slide-in-from-right md:inset-y-0 md:left-auto md:right-0 md:w-[420px] md:border-l"
          >
            <header className="flex items-center gap-2 border-b px-3 py-2">
              <AppLogo className="size-5 text-primary" />
              <Dialog.Title className="flex-1 text-sm font-semibold">
                Lesson assistant
              </Dialog.Title>
              <Dialog.Description className="sr-only">
                Ask the assistant to find, book, open or cancel lessons. You
                approve every change.
              </Dialog.Description>
              <TooltipIconButton
                tooltip="New chat"
                variant="ghost"
                disabled={!threadId && stream.messages.length === 0}
                onClick={newChat}
              >
                <SquarePen className="size-4" />
              </TooltipIconButton>
              <Dialog.Close asChild>
                <TooltipIconButton
                  tooltip="Close"
                  variant="ghost"
                >
                  <X className="size-4" />
                </TooltipIconButton>
              </Dialog.Close>
            </header>
            {stream.error != null && !stream.isLoading && (
              <div
                role="alert"
                className="flex items-start gap-2 border-b bg-destructive/10 px-3 py-2 text-xs text-red-300"
              >
                <AlertCircle className="mt-0.5 size-3.5 shrink-0" />
                <span>
                  {errorMessage(stream.error)} Is the agent running at{" "}
                  <code>{AGENT_URL}</code>?
                </span>
              </div>
            )}
            <Conversation onPick={sendMessage} />
            <Composer
              disabledReason={disabledReason}
              inputRef={inputRef}
            />
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </>
  );
}
