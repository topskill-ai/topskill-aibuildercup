import type { ButtonHTMLAttributes, ReactNode, Ref } from "react";
import { cn } from "@/lib/cn";
import { Spinner } from "./Spinner";

export type ButtonVariant = "primary" | "secondary" | "outline" | "ghost" | "danger" | "link" | "cta" | "inverse";
export type ButtonSize = "sm" | "md" | "lg" | "xl" | "icon" | "icon-sm";

const base =
  "inline-flex shrink-0 items-center justify-center gap-2 whitespace-nowrap rounded-control " +
  "transition-[color,background-color,border-color,box-shadow,translate] duration-150 ease-out select-none disabled:pointer-events-none disabled:opacity-50 " +
  "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary";

const variants: Record<ButtonVariant, string> = {
  primary: "bg-primary-solid text-primary-fg hover:bg-primary-solid-hover",
  secondary: "bg-primary-soft text-primary hover:bg-primary/15",
  outline: "border border-border bg-surface text-text shadow-soft hover:border-border-strong hover:bg-sunken",
  ghost: "text-muted hover:bg-mist hover:text-text",
  danger: "bg-danger text-on-accent hover:bg-danger/90",
  link: "h-auto px-0 font-medium text-primary underline-offset-4 hover:underline",
  /** Marketing call to action: ink in light, indigo glow in dark. */
  cta:
    "bg-cta text-primary-fg shadow-cta hover:-translate-y-0.5 hover:bg-cta-hover hover:shadow-cta-hover",
  /** Frosted white button on the brand gradient (Wallet balance card), same look in both themes. */
  inverse: "bg-primary-fg/90 text-brand-from shadow-soft hover:bg-primary-fg",
};

const sizes: Record<ButtonSize, string> = {
  sm: "h-8 px-3 text-[13px] font-medium",
  md: "h-9 px-4 text-sm font-medium",
  lg: "h-11 px-5 text-[15px] font-medium",
  xl: "h-12 px-6 text-[15px] font-semibold",
  icon: "h-9 w-9 p-0 font-medium",
  "icon-sm": "h-8 w-8 p-0 font-medium",
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
