import { useState } from "react";
import { Check, Copy, XIcon } from "lucide-react";
import { useStreamContext } from "@/providers/Stream";
import { TooltipIconButton } from "./tooltip-icon-button";

/**
 * Side panel rendering the thread's current graph state as raw JSON, for
 * debugging what the agent holds between turns.
 */
export function StatePanel({ onClose }: { onClose: () => void }) {
  const { values } = useStreamContext();
  const [copied, setCopied] = useState(false);

  const json =
    values && Object.keys(values).length > 0
      ? JSON.stringify(values, null, 2)
      : null;

  const handleCopy = () => {
    if (!json) return;
    navigator.clipboard.writeText(json).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  };

  return (
    <div className="absolute inset-0 flex min-w-[30vw] flex-col">
      <div className="flex items-center justify-between gap-2 border-b p-4">
        <span className="truncate font-medium">Agent state</span>
        <div className="flex items-center gap-1">
          <TooltipIconButton
            tooltip={copied ? "Copied" : "Copy JSON"}
            variant="ghost"
            disabled={!json}
            onClick={handleCopy}
          >
            {copied ? (
              <Check className="size-4" />
            ) : (
              <Copy className="size-4" />
            )}
          </TooltipIconButton>
          <TooltipIconButton
            tooltip="Close"
            variant="ghost"
            onClick={onClose}
          >
            <XIcon className="size-4" />
          </TooltipIconButton>
        </div>
      </div>
      <div className="scrollbar flex-grow overflow-auto p-4">
        {json ? (
          <pre className="whitespace-pre-wrap break-words font-mono text-xs leading-relaxed">
            {json}
          </pre>
        ) : (
          <p className="text-sm text-muted-foreground">
            No state yet. It appears here once the thread has a run.
          </p>
        )}
      </div>
    </div>
  );
}
