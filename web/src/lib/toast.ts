import { toast } from "sonner";
import { errorMessage, errorTraceId } from "@/api/errors";

/** Show an error toast with the trace id in small text (SPEC §11.3 trust cues, §11.6). */
export function toastApiError(err: unknown, fallbackTitle?: string): void {
  const traceId = errorTraceId(err);
  const message = errorMessage(err);
  toast.error(fallbackTitle ?? message, {
    description: [fallbackTitle ? message : null, traceId ? `Trace ID: ${traceId}` : null]
      .filter(Boolean)
      .join(" · ") || undefined,
  });
}

export { toast };
