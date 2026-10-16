import { Search, SearchX, Store, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router";
import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorLine } from "@/components/ui/ErrorLine";
import { Input } from "@/components/ui/Input";
import { Skeleton, SkeletonText } from "@/components/ui/Skeleton";
import { AgentCard } from "./AgentCard";
import { usePublicAgents } from "./api";

const GRID = "grid grid-cols-[repeat(auto-fill,minmax(min(100%,300px),1fr))] gap-5 lg:gap-[22px]";
const SEARCH_DEBOUNCE_MS = 300;

/** Debounced copy of `value`. */
function useDebounced<T>(value: T, ms: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return debounced;
}

function GridSkeleton() {
  return (
    <div role="status" aria-label="Loading agents" className={GRID}>
      {Array.from({ length: 6 }, (_, i) => (
        <div key={i} className="rounded-card border border-border bg-surface p-6">
          <div className="mb-4 flex items-center gap-3">
            <Skeleton className="h-12 w-12 rounded-full" />
            <div className="flex-1 space-y-2">
              <Skeleton className="h-4 w-2/3" />
              <Skeleton className="h-3 w-1/2" />
            </div>
          </div>
          <SkeletonText lines={2} />
          <Skeleton className="mt-5 h-6 w-28 rounded-chip" />
        </div>
      ))}
    </div>
  );
}

/**
 * Marketplace grid with search (SPEC §11.4). The search string lives in the URL (`?q=`) so a
 * search can be shared and survives reloads; requests are debounced.
 */
export function Marketplace() {
  const [params, setParams] = useSearchParams();
  const urlQ = params.get("q") ?? "";
  const [input, setInput] = useState(urlQ);
  const q = useDebounced(input.trim(), SEARCH_DEBOUNCE_MS);

  // The URL changed from outside (back button, nav link): adopt it as the new input.
  const [seenUrlQ, setSeenUrlQ] = useState(urlQ);
  if (urlQ !== seenUrlQ) {
    setSeenUrlQ(urlQ);
    if (urlQ.trim() !== q) setInput(urlQ);
  }

  // Push the debounced query into the URL (replace, so typing does not flood history).
  const pushedQ = useRef(urlQ.trim());
  useEffect(() => {
    if (q === pushedQ.current) return;
    pushedQ.current = q;
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (q) next.set("q", q);
        else next.delete("q");
        return next;
      },
      { replace: true, preventScrollReset: true },
    );
  }, [q, setParams]);

  const query = usePublicAgents(q);
  const agents = query.data?.pages.flatMap((p) => p.items) ?? [];

  let content;
  if (query.isPending) {
    content = <GridSkeleton />;
  } else if (query.isError && agents.length === 0) {
    content = (
      <div className="rounded-card border border-border bg-surface p-5">
        <ErrorLine error={query.error} onRetry={() => void query.refetch()} />
      </div>
    );
  } else if (agents.length === 0) {
    content = q ? (
      <EmptyState
        icon={<SearchX />}
        title={`No agents match “${q}”`}
        description="Try a different word, or browse every agent."
        action={
          <Button variant="outline" onClick={() => setInput("")}>
            Clear search
          </Button>
        }
      />
    ) : (
      <EmptyState
        icon={<Store />}
        title="No agents published yet"
        description="Be the first: publish a skill and it shows up here."
      />
    );
  } else {
    content = (
      <>
        <ul className={GRID} aria-label="Agents">
          {agents.map((a) => (
            <li key={a.slug} className="flex min-w-0">
              <div className="flex w-full min-w-0 [&>a]:w-full">
                <AgentCard agent={a} />
              </div>
            </li>
          ))}
        </ul>
        {query.hasNextPage ? (
          <div className="mt-6 flex justify-center">
            <Button variant="outline" loading={query.isFetchingNextPage} onClick={() => void query.fetchNextPage()}>
              Load more
            </Button>
          </div>
        ) : null}
        {query.isFetchNextPageError ? <ErrorLine className="mt-4" error={query.error} /> : null}
      </>
    );
  }

  return (
    <section aria-labelledby="marketplace-title" className="min-w-0">
      <div className="mb-7 flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between sm:gap-6">
        <div className="min-w-0">
          <h2 id="marketplace-title" className="text-2xl font-bold tracking-[-0.024em] sm:text-[29px]">
            Marketplace
          </h2>
          <p className="mt-1.5 text-[15px] text-muted">Expert agents you can chat with now, or hire from your own AI agent.</p>
        </div>
        <form
          role="search"
          className="relative w-full sm:w-[280px] sm:shrink-0"
          onSubmit={(e) => {
            e.preventDefault();
          }}
        >
          <Search aria-hidden className="pointer-events-none absolute top-1/2 left-4 h-4 w-4 -translate-y-1/2 text-faint" />
          <Input
            type="search"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Search agents"
            aria-label="Search agents"
            className="pr-9 pl-10 [&::-webkit-search-cancel-button]:hidden"
            maxLength={100}
          />
          {input ? (
            <button
              type="button"
              aria-label="Clear search"
              onClick={() => setInput("")}
              className="absolute top-1/2 right-2 -translate-y-1/2 rounded-control p-1 text-subtle hover:bg-mist hover:text-text"
            >
              <X aria-hidden className="h-4 w-4" />
            </button>
          ) : null}
        </form>
      </div>
      {content}
    </section>
  );
}
