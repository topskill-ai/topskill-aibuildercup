import { cn } from "@/lib/cn";

export interface SpinnerProps {
  className?: string;
  /** Pixel size (default 16). */
  size?: number;
  label?: string;
}

export function Spinner({ className, size = 16, label = "Loading" }: SpinnerProps) {
  return (
    <svg
      role="status"
      aria-label={label}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      className={cn("animate-spin text-current", className)}
    >
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeOpacity="0.2" strokeWidth="3" />
      <path d="M21 12a9 9 0 0 0-9-9" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
    </svg>
  );
}
