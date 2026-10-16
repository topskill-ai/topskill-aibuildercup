import logoMark from "@/assets/logo-mark.png";
import { cn } from "@/lib/cn";

/** Pixel "T" mark; on the dark theme it sits on the light `brand-tile` so the ink pixels stay visible. */
export function LogoMark({ size = 30, className }: { size?: number; className?: string }) {
  return (
    <img
      src={logoMark}
      alt=""
      width={size}
      height={size}
      className={cn(
        "block shrink-0 bg-brand-tile",
        size >= 28 ? "dark:rounded-[9px] dark:p-[3px]" : "dark:rounded-[6px] dark:p-[2px]",
        className,
      )}
    />
  );
}
