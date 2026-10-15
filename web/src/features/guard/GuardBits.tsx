/**
 * P2 guard (SPEC §18 "Guard"): "Protected by Model Armor" badge and the per-message screening note.
 */
import { ShieldAlert, ShieldCheck } from "lucide-react";
import type { GuardData } from "@/api/types";
import { Badge } from "@/components/ui/Badge";
import { cn } from "@/lib/cn";
import { guardNote } from "./guard";

export function ModelArmorBadge({ className }: { className?: string }) {
  return (
    <Badge
      variant="success"
      icon={<ShieldCheck aria-hidden className="h-3 w-3" />}
      className={className}
      title="Messages and answers are screened by Google Cloud Model Armor for prompt injection, unsafe content and sensitive data."
      data-testid="model-armor-badge"
    >
      Protected by Model Armor
    </Badge>
  );
}

export function GuardNote({ guard, className }: { guard: GuardData; className?: string }) {
  const blocked = guard.action === "blocked";
  const Icon = blocked ? ShieldAlert : ShieldCheck;
  return (
    <p
      className={cn("flex items-start gap-1.5 text-xs", blocked ? "text-danger" : "text-subtle", className)}
      data-testid="guard-note"
    >
      <Icon aria-hidden className="mt-0.5 h-3.5 w-3.5 shrink-0" />
      <span>{guardNote(guard)}</span>
    </p>
  );
}
