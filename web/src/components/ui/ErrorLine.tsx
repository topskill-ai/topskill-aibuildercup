import { AlertCircle } from "lucide-react";
import type { ReactNode } from "react";
import { errorMessage, errorTraceId } from "@/api/errors";
import { cn } from "@/lib/cn";
import { Button } from "./Button";

export interface ErrorLineProps {
  /** Any thrown value; ApiError contributes its message and trace id. */
  error?: unknown;
  /** Overrides the message derived from `error`. */
  message?: ReactNode;
  traceId?: string;
  onRetry?: () => void;
  className?: string;
}

/** Inline error: message plus the trace id in 11px subtle text (SPEC §11.3, §11.6). */
export function ErrorLine({ error, message, traceId, onRetry, className }: ErrorLineProps) {
  const text = message ?? errorMessage(error);
  const trace = traceId ?? errorTraceId(error);
  return (
    <div role="alert" className={cn("flex items-start gap-2 text-sm text-danger-text", className)}>
      <AlertCircle aria-hidden className="mt-0.5 h-4 w-4 shrink-0" />
      <div className="min-w-0 flex-1">
        <p className="break-words">{text}</p>
        {trace ? (
          <p className="mt-0.5 font-mono text-2xs break-all text-subtle" data-testid="trace-id">
            Trace ID: {trace}
          </p>
        ) : null}
      </div>
      {onRetry ? (
        <Button variant="outline" size="sm" onClick={onRetry} className="shrink-0">
          Retry
        </Button>
      ) : null}
    </div>
  );
}
