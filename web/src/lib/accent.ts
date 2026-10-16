import type { AccentColor } from "@/api/types";

/**
 * Static class names per agent accent (SPEC §11.3). Written out in full so Tailwind can see them;
 * never build these class names with string interpolation.
 */
export interface AccentClasses {
  /** Solid accent background (chips, dots). */
  solid: string;
  /** 50-tint background (agent header band, soft chips). */
  soft: string;
  /** Accent-colored text. */
  text: string;
  /** Avatar ring color. */
  ring: string;
  /** Accent border. */
  border: string;
}

export const ACCENTS: Record<AccentColor, AccentClasses> = {
  amber: {
    solid: "bg-accent-amber",
    soft: "bg-accent-amber-soft",
    text: "text-accent-amber-text",
    ring: "ring-accent-amber",
    border: "border-accent-amber",
  },
  blue: {
    solid: "bg-accent-blue",
    soft: "bg-accent-blue-soft",
    text: "text-accent-blue-text",
    ring: "ring-accent-blue",
    border: "border-accent-blue",
  },
  emerald: {
    solid: "bg-accent-emerald",
    soft: "bg-accent-emerald-soft",
    text: "text-accent-emerald-text",
    ring: "ring-accent-emerald",
    border: "border-accent-emerald",
  },
  rose: {
    solid: "bg-accent-rose",
    soft: "bg-accent-rose-soft",
    text: "text-accent-rose-text",
    ring: "ring-accent-rose",
    border: "border-accent-rose",
  },
  violet: {
    solid: "bg-accent-violet",
    soft: "bg-accent-violet-soft",
    text: "text-accent-violet-text",
    ring: "ring-accent-violet",
    border: "border-accent-violet",
  },
};

export const ACCENT_COLORS: AccentColor[] = ["amber", "blue", "emerald", "rose", "violet"];

export function accentClasses(color: AccentColor | null | undefined): AccentClasses {
  return ACCENTS[color ?? "blue"] ?? ACCENTS.blue;
}
