import { useState } from "react";
import type { AccentColor } from "@/api/types";
import { accentClasses } from "@/lib/accent";
import { cn } from "@/lib/cn";
import { initials } from "@/lib/format";

export interface AvatarProps {
  src?: string | null;
  name: string;
  accent?: AccentColor;
  /** Pixel size (default 40). */
  size?: number;
  /** Draw an accent ring (agent avatars). */
  ring?: boolean;
  className?: string;
}

/** Image avatar with a monogram fallback (also used when the image fails to load). */
export function Avatar({ src, name, accent = "blue", size = 40, ring = false, className }: AvatarProps) {
  const [failedSrc, setFailedSrc] = useState<string | null>(null);
  const a = accentClasses(accent);
  const showImage = Boolean(src) && failedSrc !== src;
  return (
    <span
      className={cn(
        "relative inline-flex shrink-0 items-center justify-center overflow-hidden rounded-full font-semibold select-none",
        showImage ? "bg-slate-100" : cn(a.soft, a.text),
        ring && cn("ring-2 ring-offset-2 ring-offset-surface", a.ring),
        className,
      )}
      style={{ width: size, height: size, fontSize: Math.max(11, Math.round(size * 0.38)) }}
    >
      {showImage ? (
        <img
          src={src ?? undefined}
          alt={name}
          width={size}
          height={size}
          loading="lazy"
          decoding="async"
          className="h-full w-full object-cover"
          onError={() => setFailedSrc(src ?? null)}
        />
      ) : (
        <span aria-label={name} role="img">
          {initials(name)}
        </span>
      )}
    </span>
  );
}
