import { useCallback, useEffect, useLayoutEffect, useRef, type Ref, type TextareaHTMLAttributes } from "react";
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

  const measure = useCallback(() => {
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
  }, [autoResize, maxRows]);

  useLayoutEffect(measure, [measure, value, props.placeholder]);

  // The height of an empty field follows its placeholder, which rewraps when the width changes
  // (narrow screens, late layout) or the web font arrives. Re-measure on width changes only.
  useEffect(() => {
    const el = inner.current;
    if (!autoResize || !el) return;
    let width = el.clientWidth;
    const ro = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(() => {
      if (el.clientWidth === width) return;
      width = el.clientWidth;
      measure();
    });
    ro?.observe(el);
    let alive = true;
    void document.fonts?.ready.then(() => {
      if (alive) measure();
    });
    return () => {
      alive = false;
      ro?.disconnect();
    };
  }, [autoResize, measure]);

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
