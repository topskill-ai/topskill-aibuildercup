import { Moon, Sun } from "lucide-react";
import { cn } from "@/lib/cn";
import { useTheme } from "@/lib/theme";

/** Moon in light mode, sun in dark mode; the accessible name says what a click does. */
export function ThemeToggle({ className }: { className?: string }) {
  const { theme, toggle } = useTheme();
  const next = theme === "dark" ? "light" : "dark";
  return (
    <button
      type="button"
      onClick={toggle}
      aria-label={`Switch to ${next} theme`}
      title={`Switch to ${next} theme`}
      className={cn(
        "flex h-[34px] w-[34px] shrink-0 items-center justify-center rounded-full border border-border bg-surface text-muted shadow-soft",
        "transition-[color,border-color,box-shadow] duration-150 hover:border-faint hover:text-text hover:shadow-lift",
        className,
      )}
    >
      {theme === "dark" ? <Sun aria-hidden className="h-4 w-4" /> : <Moon aria-hidden className="h-4 w-4" />}
    </button>
  );
}
