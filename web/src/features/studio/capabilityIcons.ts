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
  Plane,
  Scale,
  Search,
  ShieldCheck,
  Sparkles,
  Users,
  type LucideIcon,
} from "lucide-react";

/** The capability icon enum of SPEC §5.2 (`CapabilityIcon`, lucide-react names). */
export const CAPABILITY_ICONS: Record<string, LucideIcon> = {
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

export const CAPABILITY_ICON_NAMES = Object.keys(CAPABILITY_ICONS);

/** Friendly names for the icon picker (what the icon stands for, not the lucide name). */
export const CAPABILITY_ICON_LABELS: Record<string, string> = {
  sparkles: "General",
  "book-open": "Guide",
  search: "Research",
  "file-text": "Document",
  briefcase: "Work",
  "graduation-cap": "Education",
  scale: "Legal",
  "heart-pulse": "Health",
  calculator: "Budget",
  globe: "International",
  "message-circle": "Conversation",
  "clipboard-list": "Checklist",
  lightbulb: "Ideas",
  "shield-check": "Compliance",
  "chart-line": "Analysis",
  users: "People",
  plane: "Travel",
  landmark: "Government",
};

export function capabilityIconLabel(name: string): string {
  return CAPABILITY_ICON_LABELS[name] ?? name.replace(/-/g, " ");
}
