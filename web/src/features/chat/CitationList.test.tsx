import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { Citation } from "@/api/types";
import { CitationList, SearchEntryPoint } from "./CitationList";
import { ToolActivity } from "./ToolActivity";

const knowledge: Citation = {
  n: 1,
  kind: "knowledge",
  title: "visa_guide.pdf",
  uri: null,
  domain: null,
  file_id: "kf_1",
  page_start: 2,
  page_end: 2,
  snippet: "Work visas require a sponsor.",
};
const range: Citation = { ...knowledge, n: 2, page_start: 3, page_end: 4, snippet: null };
const web: Citation = {
  n: 3,
  kind: "web",
  title: "gov.br",
  uri: "https://vertexaisearch.cloud.google.com/grounding-api-redirect/abc",
  domain: "gov.br",
  file_id: null,
  page_start: null,
  page_end: null,
  snippet: null,
};

describe("CitationList", () => {
  it("shows knowledge files with page numbers and web sources as outbound domain chips", async () => {
    render(<CitationList messageId="m1" citations={[knowledge, range, web]} />);
    expect(screen.getByRole("button", { name: "Source 1: visa_guide.pdf · p. 2" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Source 2: visa_guide.pdf · pp. 3–4" })).toBeInTheDocument();
    const link = screen.getByRole("link", { name: /Web source 3: gov.br/ });
    expect(link).toHaveAttribute("href", web.uri);
    expect(link).toHaveAttribute("target", "_blank");
    expect(link.getAttribute("rel")).toContain("noopener");

    // The snippet appears on focus / tap.
    await userEvent.click(screen.getByRole("button", { name: /Source 1/ }));
    expect(screen.getByRole("tooltip")).toHaveTextContent("Work visas require a sponsor.");
  });

  it("renders nothing without sources", () => {
    const { container } = render(<CitationList messageId="m1" citations={[]} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("SearchEntryPoint", () => {
  it("renders Google's HTML only inside a sandboxed srcdoc iframe", () => {
    const html = '<style>.x{}</style><div class="x"><a href="https://www.google.com/search?q=v">visa</a></div><script>alert(1)</script>';
    const { container } = render(<SearchEntryPoint html={html} />);
    const iframe = screen.getByTitle("Google Search suggestions");
    expect(iframe.tagName).toBe("IFRAME");
    expect(iframe).toHaveAttribute("sandbox", "allow-popups allow-popups-to-escape-sandbox");
    expect(iframe.getAttribute("srcdoc")).toBe(`<base target="_blank">${html}`);
    expect(screen.getByText("Grounded with Google Search")).toBeInTheDocument();
    // Nothing from the HTML is injected into our own DOM.
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("a")).toBeNull();
  });
});

describe("ToolActivity", () => {
  it("shows a one-line summary that expands into one line per tool", async () => {
    render(
      <ToolActivity
        tools={[
          { id: "1", tool: "search_knowledge", kind: "search", title: "Searched the knowledge base", target: "visa fees", status: "completed" },
          { id: "web-0", tool: "google_search", kind: "web", title: "Searched the web", target: "brazil visa", status: "completed" },
          { id: "3", tool: "save_record", kind: "record", title: "Saved notes", target: "3 fields", status: "completed" },
        ]}
      />,
    );
    const toggle = screen.getByRole("button", { expanded: false });
    expect(toggle).toHaveTextContent("Searched the knowledge base · Searched the web · Saved notes");
    await userEvent.click(toggle);
    expect(screen.getByRole("button", { expanded: true })).toBeInTheDocument();
    expect(screen.getByText("· 3 fields")).toBeInTheDocument();
    expect(screen.getAllByLabelText("Completed")).toHaveLength(3);
  });
});
