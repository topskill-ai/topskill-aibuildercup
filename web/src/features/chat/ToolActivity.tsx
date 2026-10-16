import {
  BookOpen,
  Check,
  ChevronDown,
  CircleSlash,
  ClipboardList,
  Globe,
  Network,
  ScrollText,
  Wrench,
  X,
  type LucideIcon,
} from "lucide-react";
import { useId, useState } from "react";
import type { ToolKind, ToolLine } from "@/api/types";
import { Spinner } from "@/components/ui/Spinner";
import { cn } from "@/lib/cn";
import { toolSummary } from "./display";

const KIND_ICONS: Record<ToolKind, LucideIcon> = {
  search: BookOpen,
  web: Globe,
  skill: ScrollText,
  record: ClipboardList,
  a2a: Network,
  other: Wrench,
};

function StatusIcon({ status }: { status: ToolLine["status"] }) {
  switch (status) {
    case "running":
      return <Spinner size={12} label="Running" />;
    case "completed":
      return <Check aria-label="Completed" className="h-3.5 w-3.5 text-success-text" />;
    case "error":
      return <X aria-label="Failed" className="h-3.5 w-3.5 text-danger-text" />;
    case "interrupted":
      return <CircleSlash aria-label="Interrupted" className="h-3.5 w-3.5 text-subtle" />;
  }
}

/**
 * Collapsed one-line summary of the tools a turn used, expandable to one line per call
 * (SPEC §11.4). Targets are already redacted by the server (§7.2).
 */
export function ToolActivity({ tools, className }: { tools: ToolLine[]; className?: string }) {
  const [open, setOpen] = useState(false);
  const listId = useId();
  if (tools.length === 0) return null;
  const running = tools.some((t) => t.status === "running");
  const summary = toolSummary(tools);
  return (
    <div className={cn("min-w-0 text-[13px] text-muted", className)} data-testid="tool-activity">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={listId}
        onClick={() => setOpen((o) => !o)}
        className="flex max-w-full items-center gap-1.5 rounded-chip bg-mist px-2.5 py-1 text-left hover:bg-border"
      >
        {running ? <Spinner size={12} label="Working" /> : <Wrench aria-hidden className="h-3.5 w-3.5 shrink-0" />}
        <span className="min-w-0 truncate">{summary}</span>
        <ChevronDown aria-hidden className={cn("h-3.5 w-3.5 shrink-0 transition-transform", open && "rotate-180")} />
      </button>
      {open ? (
        <ul id={listId} className="mt-2 space-y-1.5 border-l-2 border-border pl-3">
          {tools.map((t) => {
            const Icon = KIND_ICONS[t.kind] ?? Wrench;
            return (
              <li key={t.id} className="flex min-w-0 items-center gap-2">
                <Icon aria-hidden className="h-3.5 w-3.5 shrink-0 text-subtle" />
                <span className="min-w-0 truncate">
                  <span className="text-text">{t.title}</span>
                  {t.target ? <span className="text-subtle"> · {t.target}</span> : null}
                </span>
                <span className="ml-auto shrink-0">
                  <StatusIcon status={t.status} />
                </span>
              </li>
            );
          })}
        </ul>
      ) : null}
    </div>
  );
}
