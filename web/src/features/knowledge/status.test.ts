import { describe, expect, it } from "vitest";
import { knowledgeBadge } from "./status";

describe("knowledgeBadge", () => {
  it("covers every state", () => {
    expect(knowledgeBadge(null).label).toBe("Knowledge: none");
    expect(knowledgeBadge({ files: 0, ready: 0, processing: 0, failed: 0 }).pending).toBe(false);
    expect(knowledgeBadge({ files: 2, ready: 1, processing: 1, failed: 0 })).toEqual({
      label: "Knowledge: 1 processing",
      variant: "warning",
      pending: true,
    });
    expect(knowledgeBadge({ files: 1, ready: 1, processing: 0, failed: 0 }).label).toBe("Knowledge: ready");
    expect(knowledgeBadge({ files: 2, ready: 1, processing: 0, failed: 1 }).label).toBe("Knowledge: ready · 1 failed");
    expect(knowledgeBadge({ files: 1, ready: 0, processing: 0, failed: 1 }).variant).toBe("danger");
  });
});
