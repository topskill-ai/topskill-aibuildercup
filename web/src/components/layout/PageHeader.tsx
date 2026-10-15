import { ChevronLeft } from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router";
import { cn } from "@/lib/cn";
import { Skeleton, SkeletonText } from "../ui/Skeleton";

export interface PageHeaderProps {
  title: ReactNode;
  description?: ReactNode;
  /** Small label above the title. */
  eyebrow?: ReactNode;
  /** Right-aligned actions; wrap below the title on mobile. */
  actions?: ReactNode;
  /** Back link shown above the title. */
  back?: { to: string; label: string };
  /** Leading visual (avatar, icon). */
  leading?: ReactNode;
  className?: string;
}

export function PageHeader({ title, description, eyebrow, actions, back, leading, className }: PageHeaderProps) {
  return (
    <header className={cn("mb-6 min-w-0", className)}>
      {back ? (
        <Link
          to={back.to}
          className="mb-3 inline-flex items-center gap-1 text-sm font-medium text-muted hover:text-text"
        >
          <ChevronLeft aria-hidden className="h-4 w-4" />
          {back.label}
        </Link>
      ) : null}
      <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-3">
        <div className="flex min-w-0 items-center gap-4">
          {leading}
          <div className="min-w-0">
            {eyebrow ? <div className="mb-1 text-xs font-semibold tracking-wide text-primary uppercase">{eyebrow}</div> : null}
            <h1 className="text-2xl leading-tight font-semibold tracking-display break-words sm:text-[28px]">{title}</h1>
            {description ? <p className="mt-1.5 max-w-2xl text-[15px] text-muted">{description}</p> : null}
          </div>
        </div>
        {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
      </div>
    </header>
  );
}

/** Standard page body: width-limited container with vertical rhythm. */
export function PageBody({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cn("page-container py-6 sm:py-8", className)}>{children}</div>;
}

/** Skeleton for a whole page while route data or auth state loads. */
export function PageLoading() {
  return (
    <div role="status" aria-label="Loading" className="page-container py-8">
      <Skeleton className="mb-3 h-8 w-64 max-w-full" />
      <Skeleton className="mb-8 h-4 w-96 max-w-full" />
      <div className="grid grid-cols-[repeat(auto-fill,minmax(min(100%,260px),1fr))] gap-4">
        {Array.from({ length: 3 }, (_, i) => (
          <div key={i} className="rounded-card border border-border bg-surface p-5">
            <SkeletonText lines={3} />
          </div>
        ))}
      </div>
    </div>
  );
}
