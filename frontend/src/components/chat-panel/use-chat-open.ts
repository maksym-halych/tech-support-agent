"use client";

import { parseAsBoolean, useQueryState } from "nuqs";

/** The chat panel's open state, kept in the URL (`?chat=1`) with the thread ID. */
export function useChatOpen() {
  return useQueryState("chat", parseAsBoolean.withDefault(false));
}
