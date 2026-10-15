import { useId, type ReactNode } from "react";
import { cn } from "@/lib/cn";

export interface SwitchProps {
  checked: boolean;
  onCheckedChange: (checked: boolean) => void;
  label?: ReactNode;
  description?: ReactNode;
  disabled?: boolean;
  className?: string;
  id?: string;
}

export function Switch({ checked, onCheckedChange, label, description, disabled, className, id }: SwitchProps) {
  const autoId = useId();
  const switchId = id ?? autoId;
  const descId = description ? `${switchId}-desc` : undefined;
  const control = (
    <button
      id={switchId}
      type="button"
      role="switch"
      aria-checked={checked}
      aria-describedby={descId}
      disabled={disabled}
      onClick={() => onCheckedChange(!checked)}
      className={cn(
        "relative inline-flex h-6 w-10 shrink-0 items-center rounded-chip transition-colors duration-150 ease-out",
        "disabled:cursor-not-allowed disabled:opacity-50",
        checked ? "bg-primary" : "bg-slate-300",
      )}
    >
      <span
        aria-hidden
        className={cn(
          "inline-block h-5 w-5 rounded-full bg-white shadow-sm transition-transform duration-150 ease-out",
          checked ? "translate-x-[18px]" : "translate-x-0.5",
        )}
      />
    </button>
  );
  if (!label && !description) return <span className={className}>{control}</span>;
  return (
    <div className={cn("flex items-start justify-between gap-4", className)}>
      <div className="min-w-0">
        {label ? (
          <label htmlFor={switchId} className="block text-sm font-medium text-text">
            {label}
          </label>
        ) : null}
        {description ? (
          <p id={descId} className="mt-0.5 text-[13px] text-muted">
            {description}
          </p>
        ) : null}
      </div>
      {control}
    </div>
  );
}
