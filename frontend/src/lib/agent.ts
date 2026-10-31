import { type Message } from "@langchain/langgraph-sdk";

export const AGENT_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:2024";
export const ASSISTANT_ID = process.env.NEXT_PUBLIC_ASSISTANT_ID ?? "agent";
export const AUTH_SCHEME = process.env.NEXT_PUBLIC_AUTH_SCHEME || undefined;

export type StateType = { messages: Message[] };

/** One action the agent wants to take, as LangChain's HITL middleware sends it. */
export interface ActionRequest {
  name: string;
  args: Record<string, unknown>;
  description?: string;
}

export interface ReviewConfig {
  action_name: string;
  allowed_decisions: ("approve" | "edit" | "reject" | "respond")[];
}

/** The interrupt value of `HumanInTheLoopMiddleware` (batched mode). */
export interface HITLRequest {
  action_requests: ActionRequest[];
  review_configs: ReviewConfig[];
}

export type Decision =
  { type: "approve" } | { type: "reject"; message?: string };

export function isHITLRequest(value: unknown): value is HITLRequest {
  return (
    !!value &&
    typeof value === "object" &&
    Array.isArray((value as HITLRequest).action_requests) &&
    Array.isArray((value as HITLRequest).review_configs)
  );
}
