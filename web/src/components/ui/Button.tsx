import type { ButtonHTMLAttributes, ReactNode, Ref } from "react";
import { cn } from "@/lib/cn";
import { Spinner } from "./Spinner";

export type ButtonVariant = "primary" | "secondary" | "outline" | "ghost" | "danger" | "link";
export type ButtonSize = "sm" | "md" | "lg" | "icon" | "icon-sm";

const base =
  "inline-flex shrink-0 items-center justify-center gap-2 whitespace-nowrap rounded-control font-medium " +
  "transition-colors duration-150 ease-out select-none disabled:pointer-events-none disabled:opacity-50 " +
  "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary";

const variants: Record<ButtonVariant, string> = {
  primary: "bg-primary text-primary-fg hover:bg-primary-hover",
  secondary: "bg-primary-soft text-primary hover:bg-indigo-100",
  outline: "border border-border bg-surface text-text hover:bg-slate-50 hover:border-border-strong",
  ghost: "text-muted hover:bg-slate-100 hover:text-text",
  danger: "bg-danger text-white hover:bg-rose-700",
  link: "h-auto px-0 text-primary underline-offset-4 hover:underline",
};

const sizes: Record<ButtonSize, string> = {
  sm: "h-8 px-3 text-[13px]",
  md: "h-9 px-4 text-sm",
  lg: "h-11 px-5 text-[15px]",
  icon: "h-9 w-9 p-0",
  "icon-sm": "h-8 w-8 p-0",
};

/** Classes for a button look-alike (e.g. a router `<Link>` styled as a button). */
// eslint-disable-next-line react-refresh/only-export-components
export function buttonClasses(opts: { variant?: ButtonVariant; size?: ButtonSize; className?: string } = {}): string {
  const variant = opts.variant ?? "primary";
  const size = variant === "link" ? undefined : (opts.size ?? "md");
  return cn(base, variants[variant], size && sizes[size], opts.className);
}

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  /** Shows a spinner and disables the button. */
  loading?: boolean;
  /** Icon rendered before the label (hidden while loading). */
  icon?: ReactNode;
  ref?: Ref<HTMLButtonElement>;
}

export function Button({
  variant = "primary",
  size = "md",
  loading = false,
  icon,
  className,
  disabled,
  children,
  type = "button",
  ref,
  ...props
}: ButtonProps) {
  return (
    <button
      ref={ref}
      type={type}
      className={buttonClasses({ variant, size, className })}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      {...props}
    >
      {loading ? <Spinner size={size === "sm" || size === "icon-sm" ? 14 : 16} /> : icon}
      {children}
    </button>
  );
}
