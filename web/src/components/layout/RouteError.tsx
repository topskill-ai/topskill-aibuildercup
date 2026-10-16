import { RefreshCw, TriangleAlert } from "lucide-react";
import { isRouteErrorResponse, Link, useRouteError } from "react-router";
import { errorTraceId } from "@/api/errors";
import { Button, buttonClasses } from "../ui/Button";

/**
 * Router error boundary: a failed lazy chunk (e.g. after a deploy) or a render error.
 * Shows a reload action and the trace id when the error came from the API.
 */
export function RouteError() {
  const error = useRouteError();
  const notFound = isRouteErrorResponse(error) && error.status === 404;
  const traceId = errorTraceId(error);
  return (
    <div className="page-container flex flex-1 items-center justify-center py-16">
      <div className="w-full max-w-md rounded-card border border-border bg-surface p-6 text-center">
        <div className="mx-auto mb-3 flex h-11 w-11 items-center justify-center rounded-full bg-danger-soft text-danger-text">
          <TriangleAlert aria-hidden className="h-5 w-5" />
        </div>
        <h1 className="text-lg font-semibold">{notFound ? "Page not found" : "Something went wrong"}</h1>
        <p className="mt-1 text-sm text-muted">
          {notFound ? "This page does not exist." : "Reload the page to try again. If it keeps happening, let us know."}
        </p>
        {traceId ? <p className="mt-2 font-mono text-2xs text-subtle">Trace ID: {traceId}</p> : null}
        <div className="mt-5 flex justify-center gap-2">
          <Link to="/" className={buttonClasses({ variant: "outline" })}>
            Home
          </Link>
          {!notFound ? (
            <Button icon={<RefreshCw className="h-4 w-4" />} onClick={() => window.location.reload()}>
              Reload
            </Button>
          ) : null}
        </div>
      </div>
    </div>
  );
}
