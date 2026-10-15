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
import { priceChipLabel, type PriceInfo } from "./pricing";

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
        <li key={f.key}>
          <Badge variant="outline" icon={<f.icon aria-hidden className="h-3 w-3" />}>
            {f.label}
          </Badge>
        </li>
      ))}
    </ul>
  );
}

export function PriceChip({ pricing, accent, className }: { pricing: PriceInfo; accent: AccentColor; className?: string }) {
  const free = pricing.mode === "free" || pricing.price_cents <= 0;
  return (
    <Badge
      accent={free ? undefined : accent}
      variant={free ? "success" : undefined}
      className={cn("font-mono tabular-nums", className)}
      data-testid="price-chip"
    >
      {priceChipLabel(pricing)}
    </Badge>
  );
}
