import { describe, expect, it } from "vitest";
import { classifyDrop, knowledgeFileProblem, mergeKnowledge, skillFormData, skillProblem, type PickedFile } from "./files";

function pf(path: string, size = 10): PickedFile {
  const name = path.split("/").pop() ?? path;
  return { file: new File(["x".repeat(size)], name), path };
}

describe("classifyDrop", () => {
  it("SKILL.md plus a loose PDF: skill + knowledge in one drop", () => {
    const r = classifyDrop([pf("SKILL.md"), pf("visa_guide.pdf"), pf("notes.md")]);
    expect(r.problems).toEqual([]);
    expect(r.skill?.kind).toBe("files");
    if (r.skill?.kind === "files") expect(r.skill.files.map((f) => f.path)).toEqual(["SKILL.md", "notes.md"]);
    expect(r.knowledge.map((f) => f.name)).toEqual(["visa_guide.pdf"]);
  });

  it("a folder keeps every file in the skill (hidden files skipped)", () => {
    const r = classifyDrop([
      pf("visa-advisor/SKILL.md"),
      pf("visa-advisor/references/fees.pdf"),
      pf("visa-advisor/.DS_Store"),
      pf("visa_guide.pdf"),
    ]);
    expect(r.skill?.kind).toBe("files");
    if (r.skill?.kind === "files")
      expect(r.skill.files.map((f) => f.path)).toEqual(["visa-advisor/SKILL.md", "visa-advisor/references/fees.pdf"]);
    expect(r.knowledge.map((f) => f.name)).toEqual(["visa_guide.pdf"]);
  });

  it("a zip is uploaded alone", () => {
    const r = classifyDrop([pf("visa-advisor.zip"), pf("guide.pdf")]);
    expect(r.skill?.kind).toBe("zip");
    expect(r.knowledge).toHaveLength(1);
  });

  it("explains a drop without SKILL.md", () => {
    const r = classifyDrop([pf("guide.pdf")]);
    expect(r.skill).toBeNull();
    expect(r.problems[0]).toMatch(/SKILL\.md/);
    expect(r.knowledge).toHaveLength(1);
  });

  it("multipart uses the relative path as the filename", () => {
    const fd = skillFormData({ kind: "files", files: [pf("visa-advisor/SKILL.md")] });
    const part = fd.getAll("files")[0] as File;
    expect(part.name).toBe("visa-advisor/SKILL.md");
  });
});

describe("limits", () => {
  it("knowledge type and size", () => {
    expect(knowledgeFileProblem(new File(["a"], "a.pdf"))).toBeNull();
    expect(knowledgeFileProblem(new File(["a"], "a.exe"))).toMatch(/PDF/);
    expect(knowledgeFileProblem(new File([], "a.pdf"))).toMatch(/empty/);
  });

  it("skill size limits", () => {
    expect(skillProblem({ kind: "files", files: [pf("SKILL.md", 300 * 1024)] })).toMatch(/too large/);
    expect(skillProblem({ kind: "files", files: [pf("SKILL.md")] })).toBeNull();
  });

  it("dedupes knowledge by name and size", () => {
    const a = new File(["aa"], "a.pdf");
    expect(mergeKnowledge([a], [new File(["aa"], "a.pdf"), new File(["b"], "b.pdf")]).map((f) => f.name)).toEqual([
      "a.pdf",
      "b.pdf",
    ]);
  });
});
