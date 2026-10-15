import { describe, expect, it, vi } from "vitest";
import { classifyDrop } from "./files";
import { loadSampleSkill, sampleUrl } from "./sample";

const ok = (body: string, type: string) => new Response(body, { status: 200, headers: { "content-type": type } });

describe("sample skill", () => {
  it("loads the skill folder plus the guide PDF, classified like a drop of both", async () => {
    const fetchImpl = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      return url.endsWith(".pdf") ? ok("%PDF-1.4", "application/pdf") : ok("---\nname: career-coach\n---\n", "text/markdown");
    });
    const picked = await loadSampleSkill(fetchImpl as unknown as typeof fetch);
    expect(fetchImpl).toHaveBeenCalledWith("/samples/career-coach/SKILL.md");
    const c = classifyDrop(picked);
    expect(c.problems).toEqual([]);
    expect(c.skill?.kind).toBe("files");
    expect(c.skill?.kind === "files" ? c.skill.files.map((p) => p.path) : []).toEqual([
      "career-coach/SKILL.md",
      "career-coach/references/interview-questions.md",
    ]);
    expect(c.knowledge.map((f) => [f.name, f.type])).toEqual([["career_coaching_guide.pdf", "application/pdf"]]);
  });

  it("fails when the hosting rewrite returns the app shell instead of a file", async () => {
    const fetchImpl = vi.fn(async () => ok("<!doctype html>", "text/html; charset=utf-8"));
    await expect(loadSampleSkill(fetchImpl as unknown as typeof fetch)).rejects.toThrow(/could not be loaded/);
  });

  it("builds URLs under the app base", () => {
    expect(sampleUrl("a.pdf", "/")).toBe("/samples/a.pdf");
    expect(sampleUrl("a.pdf", "/app")).toBe("/app/samples/a.pdf");
  });
});
