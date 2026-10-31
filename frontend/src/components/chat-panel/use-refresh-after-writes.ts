"use client";

import type { Message } from "@langchain/langgraph-sdk";
import { useEffect, useRef } from "react";
import { successfulWriteIds } from "./content";

/**
 * Calls `refresh` when a run finishes that booked, created or cancelled a
 * lesson, so the dashboard shows the change without a reload. Writes already
 * in a thread's history when it loads do not trigger a refresh.
 */
export function useRefreshAfterWrites(
  messages: Message[],
  isLoading: boolean,
  refresh: () => Promise<void>,
): void {
  const seen = useRef(new Set<string>());
  const runInProgress = useRef(false);

  useEffect(() => {
    if (isLoading) {
      runInProgress.current = true;
      return;
    }
    const fresh = successfulWriteIds(messages).filter(
      (id) => !seen.current.has(id),
    );
    fresh.forEach((id) => seen.current.add(id));
    if (runInProgress.current && fresh.length > 0) void refresh();
    runInProgress.current = false;
  }, [messages, isLoading, refresh]);
}
