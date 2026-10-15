import { useId, useState, type ReactNode } from "react";
import { cn } from "@/lib/cn";

export interface TooltipProps {
  content: ReactNode;
  children: ReactNode;
  side?: "top" | "bottom";
  className?: string;
}

/**
 * Lightweight tooltip shown on hover and keyboard focus (and on tap, via focus) of its child.
 * The child should be focusable (button, link) for keyboard users.
 */
export function Tooltip({ content, children, side = "top", className }: TooltipProps) {
  const id = useId();
  const [open, setOpen] = useState(false);
  return (
    <span
      className={cn("relative inline-flex", className)}
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
      onFocus={() => setOpen(true)}
      onBlur={() => setOpen(false)}
      onKeyDown={(e) => e.key === "Escape" && setOpen(false)}
      aria-describedby={open ? id : undefined}
    >
      {children}
      {open ? (
        <span
          role="tooltip"
          id={id}
          className={cn(
            "pointer-events-none absolute left-1/2 z-40 w-max max-w-[min(280px,80vw)] -translate-x-1/2 rounded-control",
            "bg-slate-900 px-2.5 py-1.5 text-xs leading-5 font-normal text-white shadow-pop animate-fade-in",
            side === "top" ? "bottom-full mb-2" : "top-full mt-2",
          )}
        >
          {content}
        </span>
      ) : null}
    </span>
  );
}
