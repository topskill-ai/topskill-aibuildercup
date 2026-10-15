import type { HTMLAttributes, ReactNode } from "react";
import type { AccentColor } from "@/api/types";
import { accentClasses } from "@/lib/accent";
import { cn } from "@/lib/cn";

export type BadgeVariant = "neutral" | "primary" | "success" | "warning" | "danger" | "outline";

const variants: Record<BadgeVariant, string> = {
  neutral: "bg-slate-100 text-muted",
  primary: "bg-primary-soft text-primary",
  success: "bg-success-soft text-success",
  warning: "bg-warning-soft text-warning",
  danger: "bg-danger-soft text-danger",
  outline: "border border-border bg-surface text-muted",
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
        "inline-flex max-w-full items-center gap-1 rounded-chip px-2.5 py-0.5 text-xs font-medium leading-5 whitespace-nowrap",
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
