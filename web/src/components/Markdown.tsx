/**
 * Markdown renderer for model answers (react-markdown + GFM; raw HTML is never rendered).
 *
 * Citation markers (SPEC §7.3): `[n]` in the text becomes a superscript button only when `n`
 * matches a knowledge citation of this answer; any other `[n]` stays plain text. Markers inside
 * links and code are left alone.
 */
import { memo, useMemo, type ComponentPropsWithoutRef, type ReactNode } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Citation } from "@/api/types";
import { cn } from "@/lib/cn";

interface MdNode {
  type: string;
  value?: string;
  children?: MdNode[];
  data?: { hName?: string; hProperties?: Record<string, unknown> };
}

const MARKER_RE = /\[(\d{1,3})\]/g;
const SKIP_PARENTS = new Set(["link", "linkReference", "inlineCode", "code", "definition"]);

/** Split one text node into text + citation nodes. Exported for tests. */
// eslint-disable-next-line react-refresh/only-export-components
export function splitCitationText(value: string, valid: ReadonlySet<number>): MdNode[] {
  const out: MdNode[] = [];
  let last = 0;
  for (const match of value.matchAll(MARKER_RE)) {
    const n = Number(match[1]);
    if (!valid.has(n)) continue;
    const start = match.index ?? 0;
    if (start > last) out.push({ type: "text", value: value.slice(last, start) });
    out.push({
      type: "citationRef",
      data: { hName: "sup", hProperties: { dataCite: String(n) } },
      children: [{ type: "text", value: String(n) }],
    });
    last = start + match[0].length;
  }
  if (out.length === 0) return [{ type: "text", value }];
  if (last < value.length) out.push({ type: "text", value: value.slice(last) });
  return out;
}

function transform(node: MdNode, valid: ReadonlySet<number>): void {
  if (!node.children || SKIP_PARENTS.has(node.type)) return;
  const next: MdNode[] = [];
  for (const child of node.children) {
    if (child.type === "text" && typeof child.value === "string" && child.value.includes("[")) {
      next.push(...splitCitationText(child.value, valid));
    } else {
      transform(child, valid);
      next.push(child);
    }
  }
  node.children = next;
}

function remarkCitations(options: { valid: ReadonlySet<number> }) {
  return (tree: MdNode) => {
    if (options.valid.size > 0) transform(tree, options.valid);
  };
}

function pageLabel(c: Citation): string {
  if (c.page_start == null) return "";
  if (c.page_end != null && c.page_end !== c.page_start) return `pp. ${c.page_start}–${c.page_end}`;
  return `p. ${c.page_start}`;
}

/** "visa_guide.pdf · p. 2" */
// eslint-disable-next-line react-refresh/only-export-components
export function citationLabel(c: Citation): string {
  const page = c.kind === "knowledge" ? pageLabel(c) : "";
  return page ? `${c.title} · ${page}` : c.title;
}

export interface MarkdownProps {
  text: string;
  /** Sources of this answer; knowledge citations turn matching `[n]` markers into buttons. */
  citations?: Citation[];
  /** Called when a citation marker is clicked (e.g. highlight it in the source list). */
  onCitationClick?: (citation: Citation) => void;
  /** Show a blinking caret after the text while streaming. */
  streaming?: boolean;
  className?: string;
}

function MarkdownImpl({ text, citations, onCitationClick, streaming, className }: MarkdownProps) {
  const byNumber = useMemo(() => {
    const map = new Map<number, Citation>();
    for (const c of citations ?? []) if (c.kind === "knowledge") map.set(c.n, c);
    return map;
  }, [citations]);
  const valid = useMemo(() => new Set(byNumber.keys()), [byNumber]);

  const components = useMemo<Components>(
    () => ({
      a: ({ node: _node, children, href, ...props }: ComponentPropsWithoutRef<"a"> & { node?: unknown }) => (
        <a {...props} href={href} target="_blank" rel="noopener noreferrer nofollow">
          {children}
        </a>
      ),
      sup: ({ node: _node, children, ...props }: ComponentPropsWithoutRef<"sup"> & { node?: unknown }) => {
        const raw = (props as Record<string, unknown>)["data-cite"];
        const n = typeof raw === "string" ? Number(raw) : NaN;
        const citation = byNumber.get(n);
        if (!citation) return <sup {...props}>{children}</sup>;
        const label = citationLabel(citation);
        return (
          <sup className="mx-[1px] align-super leading-none">
            <button
              type="button"
              data-citation={n}
              title={label}
              aria-label={`Source ${n}: ${label}`}
              onClick={() => onCitationClick?.(citation)}
              className="inline-flex h-[18px] min-w-[18px] items-center justify-center rounded-chip bg-primary-soft px-1 font-mono text-[11px] font-medium text-primary hover:bg-indigo-100"
            >
              {n}
            </button>
          </sup>
        );
      },
    }),
    [byNumber, onCitationClick],
  );

  const remarkPlugins = useMemo(() => [remarkGfm, [remarkCitations, { valid }]] as never, [valid]);

  return (
    <div className={cn("prose-answer", streaming && "[&>*:last-child]:streaming-caret", className)}>
      <ReactMarkdown remarkPlugins={remarkPlugins} components={components}>
        {text}
      </ReactMarkdown>
      {streaming && !text ? <span className="streaming-caret" aria-hidden /> : null}
    </div>
  );
}

export const Markdown = memo(MarkdownImpl) as (props: MarkdownProps) => ReactNode;
