import { useCallback, useLayoutEffect, useRef, type Ref, type TextareaHTMLAttributes } from "react";
import { cn } from "@/lib/cn";
import { controlClasses } from "./Input";

export interface TextareaProps extends TextareaHTMLAttributes<HTMLTextAreaElement> {
  invalid?: boolean;
  /** Grow with the content up to `maxRows` (composer, long fields). */
  autoResize?: boolean;
  maxRows?: number;
  ref?: Ref<HTMLTextAreaElement>;
}

export function Textarea({
  className,
  invalid,
  autoResize = false,
  maxRows = 10,
  rows = 3,
  ref,
  value,
  ...props
}: TextareaProps) {
  const inner = useRef<HTMLTextAreaElement | null>(null);

  const setRefs = useCallback(
    (node: HTMLTextAreaElement | null) => {
      inner.current = node;
      if (typeof ref === "function") ref(node);
      else if (ref) (ref as { current: HTMLTextAreaElement | null }).current = node;
    },
    [ref],
  );

  useLayoutEffect(() => {
    const el = inner.current;
    if (!autoResize || !el) return;
    el.style.height = "auto";
    const style = getComputedStyle(el);
    const line = parseFloat(style.lineHeight) || 24;
    const padding = parseFloat(style.paddingTop) + parseFloat(style.paddingBottom);
    const border = parseFloat(style.borderTopWidth) + parseFloat(style.borderBottomWidth);
    const max = line * maxRows + padding + border;
    el.style.height = `${Math.min(el.scrollHeight + border, max)}px`;
    el.style.overflowY = el.scrollHeight + border > max ? "auto" : "hidden";
  }, [autoResize, maxRows, value]);

  return (
    <textarea
      ref={setRefs}
      rows={rows}
      value={value}
      aria-invalid={invalid || undefined}
      className={cn(controlClasses, "py-2 leading-6", autoResize ? "resize-none" : "resize-y", className)}
      {...props}
    />
  );
}
