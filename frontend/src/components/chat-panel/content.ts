import type { Message } from "@langchain/langgraph-sdk";

/** Tools that change lessons; each one needs the user's approval. */
export const WRITE_TOOLS = new Set([
  "book_lesson",
  "create_lesson",
  "cancel_lesson",
]);

const TOOL_ACTIVITY: Record<string, string> = {
  get_current_datetime: "Checking the date",
  list_my_lessons: "Looking at your lessons",
  list_teachers: "Looking up teachers",
  find_available_lessons: "Searching open lessons",
  book_lesson: "Booking the lesson",
  create_lesson: "Opening the lesson",
  cancel_lesson: "Cancelling",
};

export const ACTION_TITLES: Record<string, string> = {
  book_lesson: "Book a lesson",
  create_lesson: "Open a new lesson",
  cancel_lesson: "Cancel a lesson",
};

/** Joins the text parts of a message's content. */
export function getContentString(content: Message["content"]): string {
  if (typeof content === "string") return content;
  return content
    .filter((c): c is { type: "text"; text: string } => c.type === "text")
    .map((c) => c.text)
    .join(" ");
}

/** A short, human label for what a tool call is doing. */
export function toolActivity(name: string): string {
  return TOOL_ACTIVITY[name] ?? "Working";
}

/**
 * IDs of tool results that changed a lesson: write tools whose result is not
 * an error (a rejected approval also comes back as an error result).
 */
export function successfulWriteIds(messages: Message[]): string[] {
  return messages
    .filter(
      (m) =>
        m.type === "tool" &&
        WRITE_TOOLS.has(m.name ?? "") &&
        m.status !== "error",
    )
    .map((m) => m.id ?? (m.type === "tool" ? m.tool_call_id : ""));
}
