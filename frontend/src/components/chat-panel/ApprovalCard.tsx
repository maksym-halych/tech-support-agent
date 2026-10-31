"use client";

import { Check, ShieldQuestion, X } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { type Decision, type HITLRequest } from "@/lib/agent";
import { ACTION_TITLES } from "./content";

type Choice = "approve" | "reject";

/**
 * Asks the user to approve or reject the actions the agent wants to take.
 * Nothing changes until the decisions are sent.
 */
export function ApprovalCard({
  request,
  disabled,
  onDecide,
}: {
  request: HITLRequest;
  disabled: boolean;
  onDecide: (decisions: Decision[]) => void;
}) {
  const actions = request.action_requests;
  const [choices, setChoices] = useState<(Choice | null)[]>(() =>
    actions.map(() => null),
  );
  const [reasons, setReasons] = useState<string[]>(() => actions.map(() => ""));
  const single = actions.length === 1;

  const send = (nextChoices: (Choice | null)[]) => {
    onDecide(
      nextChoices.map((choice, index) =>
        choice === "approve"
          ? { type: "approve" }
          : {
              type: "reject",
              ...(reasons[index].trim() && { message: reasons[index].trim() }),
            },
      ),
    );
  };

  const choose = (index: number, choice: Choice) => {
    const next = choices.map((current, i) => (i === index ? choice : current));
    setChoices(next);
    // A single approval goes straight out; a rejection waits for an optional reason.
    if (single && choice === "approve") send(next);
  };

  const allChosen = choices.every((choice) => choice !== null);
  const needsSend = !single || choices[0] === "reject";

  return (
    <div
      className="flex flex-col gap-3 rounded-xl border border-primary/40 bg-primary/5 p-3"
      role="group"
      aria-label="Approval needed"
    >
      <div className="flex items-center gap-2 text-sm font-medium">
        <ShieldQuestion className="size-4 text-primary" />
        {single ? "Approve this action?" : "Approve these actions?"}
      </div>
      {actions.map((action, index) => (
        <div
          key={`${action.name}-${index}`}
          className="flex flex-col gap-2 rounded-lg bg-background/60 p-3"
        >
          <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            {ACTION_TITLES[action.name] ?? action.name}
          </p>
          <p className="text-sm">
            {action.description ?? JSON.stringify(action.args)}
          </p>
          <div className="flex gap-2">
            <Button
              size="sm"
              variant={choices[index] === "approve" ? "default" : "secondary"}
              disabled={disabled}
              onClick={() => choose(index, "approve")}
            >
              <Check />
              Approve
            </Button>
            <Button
              size="sm"
              variant={
                choices[index] === "reject" ? "destructive" : "secondary"
              }
              disabled={disabled}
              onClick={() => choose(index, "reject")}
            >
              <X />
              Reject
            </Button>
          </div>
          {choices[index] === "reject" && (
            <input
              className={cn(
                "rounded-md border bg-transparent px-3 py-1.5 text-sm outline-none",
                "placeholder:text-muted-foreground focus-visible:ring-1 focus-visible:ring-ring",
              )}
              placeholder="What should change? (optional)"
              value={reasons[index]}
              disabled={disabled}
              onChange={(event) =>
                setReasons((current) =>
                  current.map((reason, i) =>
                    i === index ? event.target.value : reason,
                  ),
                )
              }
              onKeyDown={(event) => {
                if (event.key === "Enter" && allChosen && !disabled) {
                  send(choices);
                }
              }}
              aria-label="Reason for rejecting"
            />
          )}
        </div>
      ))}
      {needsSend && (
        <Button
          size="sm"
          className="self-end"
          disabled={disabled || !allChosen}
          onClick={() => send(choices)}
        >
          {single ? "Send rejection" : "Send decisions"}
        </Button>
      )}
    </div>
  );
}
