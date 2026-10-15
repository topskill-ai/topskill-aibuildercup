import type { ReactNode } from "react";
import { cn } from "@/lib/cn";
import { Skeleton } from "./Skeleton";

export interface StatProps {
  label: ReactNode;
  value: ReactNode;
  hint?: ReactNode;
  icon?: ReactNode;
  loading?: boolean;
  className?: string;
}

/** KPI tile (dashboard, agent overview). */
export function Stat({ label, value, hint, icon, loading, className }: StatProps) {
  return (
    <div className={cn("min-w-0 rounded-card border border-border bg-surface p-4", className)}>
      <div className="flex items-center justify-between gap-2 text-[13px] font-medium text-muted">
        <span className="truncate">{label}</span>
        {icon ? <span className="shrink-0 text-subtle [&_svg]:h-4 [&_svg]:w-4">{icon}</span> : null}
      </div>
      <div className="mt-1.5 truncate text-2xl font-semibold tracking-display text-text">
        {loading ? <Skeleton className="h-7 w-24" /> : value}
      </div>
      {hint ? <div className="mt-1 truncate text-xs text-subtle">{hint}</div> : null}
    </div>
  );
}
