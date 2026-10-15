import { Sparkles } from "lucide-react";
import { CAPABILITY_ICONS } from "./capabilityIcons";

export function CapabilityIcon({ name, className }: { name: string; className?: string }) {
  const Icon = CAPABILITY_ICONS[name] ?? Sparkles;
  return <Icon aria-hidden className={className} />;
}
