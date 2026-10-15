/** Knowledge readiness summary (SPEC §11.4 Overview badge: "Knowledge: ready / N processing"). */
import type { BadgeVariant } from "@/components/ui/Badge";

export interface KnowledgeCounts {
  files: number;
  ready: number;
  processing: number;
  failed: number;
}

export interface KnowledgeBadgeInfo {
  label: string;
  variant: BadgeVariant;
  /** True while at least one file is still being indexed (callers poll). */
  pending: boolean;
}

export function knowledgeBadge(k: KnowledgeCounts | null | undefined): KnowledgeBadgeInfo {
  if (!k || k.files === 0) return { label: "Knowledge: none", variant: "neutral", pending: false };
  if (k.processing > 0) {
    return { label: `Knowledge: ${k.processing} processing`, variant: "warning", pending: true };
  }
  if (k.ready > 0 && k.failed === 0) return { label: "Knowledge: ready", variant: "success", pending: false };
  if (k.ready > 0) return { label: `Knowledge: ready · ${k.failed} failed`, variant: "warning", pending: false };
  return { label: "Knowledge: failed", variant: "danger", pending: false };
}
