import { cn } from "@/lib/utils";
import * as React from "react";

export interface TextAreaProps extends React.InputHTMLAttributes<HTMLTextAreaElement> {
  growing?: boolean;
  maxRows?: number;
}

const TextArea = React.forwardRef<HTMLTextAreaElement, TextAreaProps>(
  ({ className, growing = false, maxRows, ...props }, ref) => {
    const textareaRef = React.useRef<HTMLTextAreaElement>(null);
    const [isOverflowing, setIsOverflowing] = React.useState(false);
    React.useImperativeHandle(ref, () => textareaRef.current!);

    const adjustHeight = React.useCallback(() => {
      const textarea = textareaRef.current;
      if (!textarea) return;

      textarea.style.height = "auto";
      const contentHeight = textarea.scrollHeight;
      const computedStyle = getComputedStyle(textarea);

      let maxHeight = Infinity;
      if (maxRows) {
        // Compute line height from the element itself
        const lineHeight =
          parseFloat(computedStyle.lineHeight) ||
          parseFloat(computedStyle.fontSize) * 1.5;
        const paddingTop = parseFloat(computedStyle.paddingTop) || 0;
        const paddingBottom = parseFloat(computedStyle.paddingBottom) || 0;
        maxHeight = maxRows * lineHeight + paddingTop + paddingBottom;
      }

      // Also honor a CSS max-height (e.g. a max-h-[20dvh] utility): whichever
      // ceiling is lower wins, so cramped surfaces can cap growth by available
      // space and the overflow/scrollbar state stays truthful either way.
      // Percentage max-heights don't resolve to px here, so they're ignored.
      if (!computedStyle.maxHeight.endsWith("%")) {
        const cssMax = parseFloat(computedStyle.maxHeight);
        if (!Number.isNaN(cssMax)) {
          maxHeight = Math.min(maxHeight, cssMax);
        }
      }

      // Inside a [data-textarea-fit] container (a bounded, overflow-clipping
      // box like a modal), never grow past the space that's actually free:
      // with our height at `auto`, the container's scrollHeight−clientHeight
      // is exactly how far we'd push siblings out of view — give that back.
      const fitEl = textarea.closest<HTMLElement>("[data-textarea-fit]");
      if (fitEl) {
        const containerOverflow = fitEl.scrollHeight - fitEl.clientHeight;
        if (containerOverflow > 0) {
          maxHeight = Math.min(maxHeight, contentHeight - containerOverflow);
        }
      }

      if (contentHeight > maxHeight) {
        textarea.style.height = `${maxHeight}px`;
        setIsOverflowing(true);
        return;
      }

      textarea.style.height = `${contentHeight}px`;
      setIsOverflowing(false);
    }, [maxRows]);

    // Auto-resize when value changes programmatically (not just on user input)
    React.useEffect(() => {
      if (growing) {
        adjustHeight();
      }
    }, [growing, props.value, adjustHeight]);

    // Viewport-relative CSS ceilings (dvh/vh) move when the window resizes;
    // re-measure so the applied height and scrollbar state track them.
    React.useEffect(() => {
      if (!growing) return;
      window.addEventListener("resize", adjustHeight);
      return () => window.removeEventListener("resize", adjustHeight);
    }, [growing, adjustHeight]);

    // A [data-textarea-fit] container's free space also changes when ITS size
    // or other content changes (video loads, sections expand) — track it.
    React.useEffect(() => {
      if (!growing) return;
      const fitEl = textareaRef.current?.closest<HTMLElement>(
        "[data-textarea-fit]",
      );
      if (!fitEl) return;
      const observer = new ResizeObserver(() => adjustHeight());
      observer.observe(fitEl);
      return () => observer.disconnect();
    }, [growing, adjustHeight]);

    return (
      <textarea
        className={cn(
          "box-border flex w-full resize-none rounded-md border border-input bg-transparent px-3 py-2 text-base shadow-sm transition-colors file:border-0 file:bg-transparent file:text-sm file:font-medium placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50",
          growing
            ? isOverflowing
              ? "scrollbar overflow-auto"
              : "overflow-hidden"
            : "overflow-auto",
          className,
        )}
        onInput={growing ? adjustHeight : undefined}
        ref={textareaRef}
        {...props}
      />
    );
  },
);

TextArea.displayName = "TextArea";
export { TextArea };
