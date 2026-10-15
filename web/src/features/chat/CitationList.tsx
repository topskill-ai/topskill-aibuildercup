import { ExternalLink, FileText, Globe, Search } from "lucide-react";
import { useState } from "react";
import type { Citation } from "@/api/types";
import { citationLabel } from "@/components/Markdown";
import { Tooltip } from "@/components/ui/Tooltip";
import { cn } from "@/lib/cn";
import { citationDomId, entryPointDocument } from "./display";

function webDomain(c: Citation): string {
  if (c.domain) return c.domain;
  if (c.title) return c.title;
  try {
    return c.uri ? new URL(c.uri).hostname : "Web source";
  } catch {
    return "Web source";
  }
}

const chip =
  "inline-flex max-w-full items-center gap-1.5 rounded-chip border border-border bg-surface px-2.5 py-1 text-[13px] " +
  "text-text transition-colors duration-150 hover:border-border-strong hover:bg-slate-50";

function Num({ n }: { n: number }) {
  return (
    <span className="inline-flex h-[18px] min-w-[18px] shrink-0 items-center justify-center rounded-chip bg-primary-soft px-1 font-mono text-[11px] font-medium text-primary">
      {n}
    </span>
  );
}

/**
 * Sources of one answer (SPEC §7.3, §11.4): knowledge files with page numbers (snippet on hover or
 * tap) and web sources as domain chips that open in a new tab.
 */
export function CitationList({
  messageId,
  citations,
  highlighted,
  className,
}: {
  messageId: string;
  citations: Citation[];
  highlighted?: number | null;
  className?: string;
}) {
  // Knowledge snippets are a disclosure (tap on phones, where hover/focus tooltips never show).
  const [openN, setOpenN] = useState<number | null>(null);
  const snippetId = `${citationDomId(messageId, 0)}-snippet`;
  const knowledge = citations.filter((c) => c.kind === "knowledge");
  const web = citations.filter((c) => c.kind === "web");
  if (knowledge.length === 0 && web.length === 0) return null;
  const opened = knowledge.find((c) => c.n === openN && c.snippet) ?? null;
  return (
    <div className={cn("min-w-0", className)} data-testid="citations">
      <div className="mb-1.5 text-xs font-semibold tracking-wide text-subtle uppercase">Sources</div>
      <ul className="flex flex-wrap gap-1.5">
        {knowledge.map((c) => {
          const label = citationLabel(c);
          const isOpen = opened?.n === c.n;
          const button = (
            <button
              type="button"
              id={citationDomId(messageId, c.n)}
              className={cn(
                chip,
                (highlighted === c.n || isOpen) && "border-primary ring-2 ring-primary/20",
                !c.snippet && "cursor-default",
              )}
              aria-label={`Source ${c.n}: ${label}`}
              aria-expanded={c.snippet ? isOpen : undefined}
              aria-controls={c.snippet && isOpen ? snippetId : undefined}
              onClick={c.snippet ? () => setOpenN((n) => (n === c.n ? null : c.n)) : undefined}
            >
              <Num n={c.n} />
              <FileText aria-hidden className="h-3.5 w-3.5 shrink-0 text-subtle" />
              <span className="min-w-0 truncate">{label}</span>
            </button>
          );
          return (
            <li key={`k-${c.n}`} className="max-w-full min-w-0">
              {c.snippet ? (
                <Tooltip content={<span className="line-clamp-6">{c.snippet}</span>} className="max-w-full">
                  {button}
                </Tooltip>
              ) : (
                button
              )}
            </li>
          );
        })}
        {web.map((c) => {
          const domain = webDomain(c);
          const inner = (
            <>
              <Num n={c.n} />
              <Globe aria-hidden className="h-3.5 w-3.5 shrink-0 text-subtle" />
              <span className="min-w-0 truncate">{domain}</span>
              {c.uri ? <ExternalLink aria-hidden className="h-3 w-3 shrink-0 text-subtle" /> : null}
            </>
          );
          return (
            <li key={`w-${c.n}`} className="max-w-full min-w-0">
              {c.uri ? (
                <a
                  href={c.uri}
                  target="_blank"
                  rel="noopener noreferrer nofollow"
                  id={citationDomId(messageId, c.n)}
                  className={chip}
                  title={c.title && c.title !== domain ? `${c.title} (${domain})` : domain}
                  aria-label={`Web source ${c.n}: ${domain} (opens in a new tab)`}
                >
                  {inner}
                </a>
              ) : (
                <span id={citationDomId(messageId, c.n)} className={chip}>
                  {inner}
                </span>
              )}
            </li>
          );
        })}
      </ul>
      {opened ? (
        <blockquote
          id={snippetId}
          className="mt-2 rounded-control border-l-2 border-primary bg-slate-50 px-3 py-2 text-[13px] leading-5 text-muted"
          data-testid="citation-snippet"
        >
          <span className="mb-0.5 block text-xs font-medium text-subtle">
            [{opened.n}] {citationLabel(opened)}
          </span>
          {opened.snippet}
        </blockquote>
      ) : null}
    </div>
  );
}

/**
 * Google Search suggestions (search entry point) shown under a grounded answer, as Google's
 * grounding display rules require (SPEC §7.3). The HTML is untrusted: it is rendered in a
 * sandboxed `srcdoc` iframe without scripts or same-origin access. Google's markup is kept as is;
 * only a `<base target="_blank">` is prepended so its links open a new tab (the frame itself may
 * not navigate, and google.com refuses to be framed anyway).
 */
export function SearchEntryPoint({ html, className }: { html: string; className?: string }) {
  return (
    <div className={cn("min-w-0", className)} data-testid="search-entry-point">
      <div className="mb-1 flex items-center gap-1.5 text-xs text-subtle">
        <Search aria-hidden className="h-3.5 w-3.5" />
        Grounded with Google Search
      </div>
      <iframe
        title="Google Search suggestions"
        srcDoc={entryPointDocument(html)}
        sandbox="allow-popups allow-popups-to-escape-sandbox"
        referrerPolicy="no-referrer"
        loading="lazy"
        // Google's carousel is ~56px tall (verified live); the rest leaves room for a scrollbar.
        className="block h-[68px] w-full rounded-control border-0 bg-transparent"
      />
    </div>
  );
}
