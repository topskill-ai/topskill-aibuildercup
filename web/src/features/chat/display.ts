/** Small display helpers shared by the chat components. */
import type { ToolLine } from "@/api/types";

/** DOM id of a source chip, so a `[n]` marker can scroll to and focus it. */
export const citationDomId = (messageId: string, n: number) => `cite-${messageId}-${n}`;

/** "Searched the knowledge base · Searched the web · Saved notes" (unique titles, in order). */
export function toolSummary(tools: ToolLine[]): string {
  const seen: string[] = [];
  for (const t of tools) if (t.title && !seen.includes(t.title)) seen.push(t.title);
  return seen.join(" · ");
}

/** `srcdoc` for the search entry point iframe: Google's HTML with links opening a new tab. */
export function entryPointDocument(html: string): string {
  return `<base target="_blank">${html}`;
}
