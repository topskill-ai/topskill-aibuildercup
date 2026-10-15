import type { AccentColor } from "@/api/types";
import { ACCENT_COLORS } from "@/lib/accent";

/** Stable accent for an agent we only know by slug (directory entries carry no accent). */
export function accentForSlug(slug: string): AccentColor {
  let h = 0;
  for (let i = 0; i < slug.length; i += 1) h = (h * 31 + slug.charCodeAt(i)) >>> 0;
  return ACCENT_COLORS[h % ACCENT_COLORS.length];
}
