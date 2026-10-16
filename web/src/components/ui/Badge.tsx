import type { HTMLAttributes, ReactNode } from "react";
import type { AccentColor } from "@/api/types";
import { accentClasses } from "@/lib/accent";
import { cn } from "@/lib/cn";

export type BadgeVariant = "neutral" | "primary" | "success" | "warning" | "danger" | "outline" | "tag";

/** Shape per variant (kept out of the base: `cn` has no tailwind-merge, so overrides would not win). */
const PILL = "rounded-chip px-2.5 text-xs font-medium";
const TAG = "rounded-[7px] px-2 text-[11.5px] font-semibold";

const variants: Record<BadgeVariant, string> = {
  neutral: "bg-mist text-muted",
  primary: "bg-primary-soft text-primary",
  success: "bg-success-soft text-success-text",
  warning: "bg-warning-soft text-warning-text",
  danger: "bg-danger-soft text-danger-text",
  outline: "border border-border bg-surface text-muted",
  /** Soft square-ish tag (marketplace feature badges). */
  tag: "bg-mist text-muted",
};

export interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  variant?: BadgeVariant;
  /** Agent accent chip (overrides `variant`). */
  accent?: AccentColor;
  icon?: ReactNode;
  /** Small colored dot before the label (status pills). */
  dot?: boolean;
}

export function Badge({ variant = "neutral", accent, icon, dot, className, children, ...props }: BadgeProps) {
  const a = accent ? accentClasses(accent) : null;
  return (
    <span
      className={cn(
        "inline-flex max-w-full items-center gap-1 py-0.5 leading-5 whitespace-nowrap",
        !a && variant === "tag" ? TAG : PILL,
        a ? cn(a.soft, a.text) : variants[variant],
        className,
      )}
      {...props}
    >
      {dot ? <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-current" /> : null}
      {icon}
      <span className="truncate">{children}</span>
    </span>
  );
}
