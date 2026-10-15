import { ChevronDown } from "lucide-react";
import type { Ref, SelectHTMLAttributes } from "react";
import { cn } from "@/lib/cn";
import { controlClasses } from "./Input";

export interface SelectOption {
  value: string;
  label: string;
  disabled?: boolean;
}

export interface SelectProps extends Omit<SelectHTMLAttributes<HTMLSelectElement>, "children"> {
  options: SelectOption[];
  placeholder?: string;
  invalid?: boolean;
  ref?: Ref<HTMLSelectElement>;
}

/** Native select (accessible, mobile-friendly) styled like the other controls. */
export function Select({ options, placeholder, invalid, className, ref, ...props }: SelectProps) {
  return (
    <div className={cn("relative min-w-0", className)}>
      <select
        ref={ref}
        aria-invalid={invalid || undefined}
        className={cn(controlClasses, "h-10 appearance-none pr-9")}
        {...props}
      >
        {placeholder ? (
          <option value="" disabled>
            {placeholder}
          </option>
        ) : null}
        {options.map((o) => (
          <option key={o.value} value={o.value} disabled={o.disabled}>
            {o.label}
          </option>
        ))}
      </select>
      <ChevronDown
        aria-hidden
        className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-subtle"
      />
    </div>
  );
}
