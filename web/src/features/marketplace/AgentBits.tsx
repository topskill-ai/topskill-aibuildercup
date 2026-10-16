/**
 * Small agent presentation pieces shared by the marketplace grid and the agent page:
 * capability icons, feature badges and the price chip.
 */
import {
  BookOpen,
  Briefcase,
  Calculator,
  ChartLine,
  ClipboardList,
  FileText,
  Globe,
  GraduationCap,
  HeartPulse,
  Landmark,
  Lightbulb,
  MessageCircle,
  Network,
  Plane,
  Scale,
  Search,
  ShieldCheck,
  Sparkles,
  Users,
  type LucideIcon,
} from "lucide-react";
import type { AccentColor, PublicAgentCard } from "@/api/types";
import { Badge } from "@/components/ui/Badge";
import { cn } from "@/lib/cn";
import { priceChipParts, type PriceInfo } from "./pricing";

/** `Capability.icon` values (lucide names, SPEC §5.2 `CapabilityIcon`) → components. */
const CAPABILITY_ICONS: Record<string, LucideIcon> = {
  sparkles: Sparkles,
  "book-open": BookOpen,
  search: Search,
  "file-text": FileText,
  briefcase: Briefcase,
  "graduation-cap": GraduationCap,
  scale: Scale,
  "heart-pulse": HeartPulse,
  calculator: Calculator,
  globe: Globe,
  "message-circle": MessageCircle,
  "clipboard-list": ClipboardList,
  lightbulb: Lightbulb,
  "shield-check": ShieldCheck,
  "chart-line": ChartLine,
  users: Users,
  plane: Plane,
  landmark: Landmark,
};

export function CapabilityIcon({ name, className }: { name: string; className?: string }) {
  const Icon = CAPABILITY_ICONS[name] ?? Sparkles;
  return <Icon aria-hidden className={cn("h-4 w-4", className)} />;
}

const FEATURES = [
  { key: "knowledge", label: "Knowledge", icon: BookOpen },
  { key: "web_search", label: "Web", icon: Globe },
  { key: "records", label: "Records", icon: ClipboardList },
  { key: "a2a", label: "A2A", icon: Network },
] as const;

export function FeatureBadges({ features, className }: { features: PublicAgentCard["features"]; className?: string }) {
  const on = FEATURES.filter((f) => features[f.key]);
  if (on.length === 0) return null;
  return (
    <ul className={cn("flex flex-wrap gap-1.5", className)} aria-label="Features">
      {on.map((f) => (
        <li key={f.key} className="inline-flex">
          <Badge variant="tag" icon={<f.icon aria-hidden className="h-3 w-3 text-faint" />}>
            {f.label}
          </Badge>
        </li>
      ))}
    </ul>
  );
}

/**
 * Price chip. `tone="accent"` (default) tints it with the agent accent (agent page header);
 * `tone="money"` is the marketplace-card look: green, bold amount, lighter unit.
 */
export function PriceChip({
  pricing,
  accent,
  tone = "accent",
  className,
}: {
  pricing: PriceInfo;
  accent: AccentColor;
  tone?: "accent" | "money";
  className?: string;
}) {
  const { amount, unit } = priceChipParts(pricing);
  const free = unit === ""; // only "Free" has no unit
  const testId = "price-chip";
  if (tone === "money") {
    return (
      <span
        className={cn(
          "inline-flex max-w-full items-baseline gap-1 truncate rounded-[8px] bg-success-soft px-2.5 py-1 text-[13px] font-semibold text-success-text",
          className,
        )}
        data-testid={testId}
      >
        <span className="text-sm tabular-nums">{amount}</span>
        {free ? null : <span className="text-xs font-medium">{unit}</span>}
      </span>
    );
  }
  return (
    <Badge
      accent={free ? undefined : accent}
      variant={free ? "success" : undefined}
      className={cn("font-mono tabular-nums", className)}
      data-testid={testId}
    >
      {amount + unit}
    </Badge>
  );
}
