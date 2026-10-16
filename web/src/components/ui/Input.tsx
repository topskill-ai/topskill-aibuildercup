import { useId, type InputHTMLAttributes, type ReactNode, type Ref } from "react";
import { cn } from "@/lib/cn";

export const controlClasses =
  "block w-full min-w-0 rounded-control border border-border bg-surface px-3 text-[15px] text-text shadow-soft " +
  "placeholder:text-subtle transition-colors duration-150 ease-out " +
  "hover:border-border-strong focus:border-primary focus:outline-none focus:ring-3 focus:ring-primary/15 " +
  "disabled:cursor-not-allowed disabled:bg-sunken disabled:text-muted " +
  "aria-[invalid=true]:border-danger aria-[invalid=true]:focus:ring-danger/15";

export interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  invalid?: boolean;
  /** Monospace text (ids, slugs, amounts). */
  mono?: boolean;
  ref?: Ref<HTMLInputElement>;
}

export function Input({ className, invalid, mono, ref, ...props }: InputProps) {
  return (
    <input
      ref={ref}
      aria-invalid={invalid || undefined}
      className={cn(controlClasses, "h-10", mono && "font-mono text-sm", className)}
      {...props}
    />
  );
}

export function Label({ htmlFor, children, className }: { htmlFor?: string; children: ReactNode; className?: string }) {
  return (
    <label htmlFor={htmlFor} className={cn("mb-1.5 block text-sm font-medium text-text", className)}>
      {children}
    </label>
  );
}

export interface FieldProps {
  label?: ReactNode;
  hint?: ReactNode;
  error?: ReactNode;
  className?: string;
  /** Render prop receiving the generated control id and aria props. */
  children: (control: { id: string; "aria-describedby"?: string; invalid: boolean }) => ReactNode;
}

/** Label + control + hint/error with wired-up ids. */
export function Field({ label, hint, error, className, children }: FieldProps) {
  const id = useId();
  const hintId = hint ? `${id}-hint` : undefined;
  const errorId = error ? `${id}-error` : undefined;
  const describedBy = [hintId, errorId].filter(Boolean).join(" ") || undefined;
  return (
    <div className={cn("min-w-0", className)}>
      {label ? <Label htmlFor={id}>{label}</Label> : null}
      {children({ id, "aria-describedby": describedBy, invalid: Boolean(error) })}
      {hint && !error ? (
        <p id={hintId} className="mt-1.5 text-[13px] text-muted">
          {hint}
        </p>
      ) : null}
      {error ? (
        <p id={errorId} className="mt-1.5 text-[13px] text-danger-text">
          {error}
        </p>
      ) : null}
    </div>
  );
}
