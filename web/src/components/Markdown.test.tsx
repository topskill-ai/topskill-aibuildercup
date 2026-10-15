import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { Citation } from "@/api/types";
import { citationLabel, Markdown, splitCitationText } from "./Markdown";

const knowledge = (n: number, extra: Partial<Citation> = {}): Citation => ({
  n,
  kind: "knowledge",
  title: "visa_guide.pdf",
  uri: null,
  domain: null,
  file_id: "kf_1",
  page_start: 2,
  page_end: 2,
  snippet: "Work visas require…",
  ...extra,
});

const web = (n: number): Citation => ({
  n,
  kind: "web",
  title: "gov.br",
  uri: "https://vertexaisearch.cloud.google.com/grounding-api-redirect/x",
  domain: "gov.br",
  file_id: null,
  page_start: null,
  page_end: null,
  snippet: null,
});

describe("Markdown citation markers", () => {
  it("renders [n] as a superscript button only for knowledge citations of this answer", () => {
    render(
      <Markdown
        text={"Fees are listed in the guide [1]. Processing takes weeks [2]. See also [3]."}
        citations={[knowledge(1), web(2)]}
      />,
    );
    const buttons = screen.getAllByRole("button");
    expect(buttons).toHaveLength(1);
    expect(buttons[0]).toHaveTextContent("1");
    expect(buttons[0]).toHaveAccessibleName("Source 1: visa_guide.pdf · p. 2");
    expect(buttons[0].closest("sup")).not.toBeNull();
    // web citation [2] and unknown [3] stay plain text
    expect(screen.getByText(/Processing takes weeks \[2\]\. See also \[3\]\./)).toBeInTheDocument();
  });

  it("calls onCitationClick with the citation", () => {
    const onClick = vi.fn();
    const c = knowledge(1);
    render(<Markdown text="Answer [1]" citations={[c]} onCitationClick={onClick} />);
    fireEvent.click(screen.getByRole("button", { name: /Source 1/ }));
    expect(onClick).toHaveBeenCalledWith(c);
  });

  it("leaves markers inside code and links untouched and renders GFM", () => {
    const { container } = render(
      <Markdown
        text={"Use `arr[1]` here.\n\n[see [1]](https://example.com) and **bold** [1]\n\n| a | b |\n|---|---|\n| 1 | 2 |"}
        citations={[knowledge(1)]}
      />,
    );
    expect(container.querySelector("code")).toHaveTextContent("arr[1]");
    const link = container.querySelector("a")!;
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer nofollow");
    expect(link.querySelector("button")).toBeNull();
    expect(screen.getAllByRole("button")).toHaveLength(1);
    expect(container.querySelector("table")).not.toBeNull();
    expect(container.querySelector("strong")).toHaveTextContent("bold");
  });

  it("does not render raw HTML", () => {
    const { container } = render(<Markdown text={'<img src=x onerror="alert(1)"> hi'} />);
    expect(container.querySelector("img")).toBeNull();
  });

  it("renders every marker when no citations are given as plain text", () => {
    render(<Markdown text="Plain [1] text" />);
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.getByText("Plain [1] text")).toBeInTheDocument();
  });
});

describe("helpers", () => {
  it("splits text around valid markers only", () => {
    const parts = splitCitationText("a [1] b [9] c [2]", new Set([1, 2]));
    expect(parts.map((p) => p.type)).toEqual(["text", "citationRef", "text", "citationRef"]);
    expect(parts[2].value).toBe(" b [9] c ");
  });

  it("labels page ranges", () => {
    expect(citationLabel(knowledge(1, { page_start: 2, page_end: 4 }))).toBe("visa_guide.pdf · pp. 2–4");
    expect(citationLabel(knowledge(1, { page_start: null, page_end: null }))).toBe("visa_guide.pdf");
    expect(citationLabel(web(3))).toBe("gov.br");
  });
});
