/**
 * Classifying what the creator drops on the wizard (SPEC §6.4 Skills, §11.4 Step 1).
 *
 * - A `.zip` is uploaded alone as `file`.
 * - A folder (files with a relative path) is the skill package; every file goes to the skill.
 * - Loose files must include one `SKILL.md`; loose documents (.pdf/.docx/.csv) dropped next to it
 *   are treated as knowledge files, so "drop SKILL.md + visa_guide.pdf" works in one gesture.
 * Knowledge files are kept in memory and uploaded one per request after publish.
 */

export interface PickedFile {
  file: File;
  /** Relative path inside a dropped/picked folder ("visa-advisor/references/x.md"), else the name. */
  path: string;
}

export const KNOWLEDGE_EXTENSIONS = [".pdf", ".txt", ".md", ".csv", ".docx"] as const;
export const KNOWLEDGE_MAX_BYTES = 10 * 1024 * 1024;
export const KNOWLEDGE_MAX_FILES = 20;
/** Loose files with these extensions go to knowledge when dropped next to a SKILL.md. */
const LOOSE_KNOWLEDGE_EXTENSIONS = [".pdf", ".docx", ".csv"];

export const SKILL_LIMITS = { files: 30, fileBytes: 2 * 1024 * 1024, totalBytes: 10 * 1024 * 1024, skillMdBytes: 200 * 1024 };

export function extensionOf(name: string): string {
  const base = name.split("/").pop() ?? name;
  const i = base.lastIndexOf(".");
  return i > 0 ? base.slice(i).toLowerCase() : "";
}

export function baseName(path: string): string {
  return path.split("/").pop() ?? path;
}

function isHidden(path: string): boolean {
  return path.split("/").some((part) => part.startsWith(".") || part === "__MACOSX");
}

export function isKnowledgeFile(name: string): boolean {
  return (KNOWLEDGE_EXTENSIONS as readonly string[]).includes(extensionOf(name));
}

/** Client-side knowledge check (the server re-validates): type and size. */
export function knowledgeFileProblem(file: File): string | null {
  if (!isKnowledgeFile(file.name)) return `${file.name}: use PDF, TXT, MD, CSV or DOCX.`;
  if (file.size > KNOWLEDGE_MAX_BYTES) return `${file.name}: files are limited to 10 MB.`;
  if (file.size === 0) return `${file.name}: the file is empty.`;
  return null;
}

export type SkillUpload =
  | { kind: "zip"; file: File }
  | { kind: "files"; files: PickedFile[] };

export interface DropClassification {
  skill: SkillUpload | null;
  knowledge: File[];
  problems: string[];
}

export function classifyDrop(picked: PickedFile[]): DropClassification {
  const visible = picked.filter((p) => !isHidden(p.path));
  const problems: string[] = [];
  if (visible.length === 0) return { skill: null, knowledge: [], problems: ["Nothing to upload."] };

  const zips = visible.filter((p) => extensionOf(p.path) === ".zip");
  if (zips.length > 0) {
    if (zips.length > 1) problems.push("Drop one .zip at a time.");
    const knowledge = visible.filter((p) => extensionOf(p.path) !== ".zip" && isKnowledgeFile(p.path)).map((p) => p.file);
    return { skill: { kind: "zip", file: zips[0].file }, knowledge, problems };
  }

  const inFolder = visible.some((p) => p.path.includes("/"));
  const skillMds = visible.filter((p) => baseName(p.path).toLowerCase() === "skill.md");
  if (skillMds.length === 0) {
    const docs = visible.filter((p) => isKnowledgeFile(p.path));
    problems.push("Add a SKILL.md, a skill folder or a .zip of the folder.");
    return { skill: null, knowledge: inFolder ? [] : docs.map((p) => p.file), problems };
  }

  // Loose documents (no folder in their path) dropped next to the skill are knowledge files; inside a
  // skill folder every file belongs to the skill package.
  const knowledge: File[] = [];
  const skillFiles: PickedFile[] = [];
  for (const p of visible) {
    const loose = !p.path.includes("/");
    if (
      loose &&
      baseName(p.path).toLowerCase() !== "skill.md" &&
      LOOSE_KNOWLEDGE_EXTENSIONS.includes(extensionOf(p.path))
    ) {
      knowledge.push(p.file);
    } else {
      skillFiles.push(p);
    }
  }
  if (!inFolder && skillMds.length > 1) problems.push("Drop only one SKILL.md.");
  return { skill: { kind: "files", files: skillFiles }, knowledge, problems };
}

/** Client-side check of the skill package against §6.4 limits (the server re-validates). */
export function skillProblem(skill: SkillUpload): string | null {
  if (skill.kind === "zip") return skill.file.size > 12 * 1024 * 1024 ? "The zip is larger than 12 MB." : null;
  if (skill.files.length > SKILL_LIMITS.files) return `A skill can have at most ${SKILL_LIMITS.files} files.`;
  let total = 0;
  for (const p of skill.files) {
    const isSkillMd = baseName(p.path).toLowerCase() === "skill.md";
    const limit = isSkillMd ? SKILL_LIMITS.skillMdBytes : SKILL_LIMITS.fileBytes;
    if (p.file.size > limit) return `${baseName(p.path)} is too large (max ${isSkillMd ? "200 KB" : "2 MB"}).`;
    total += p.file.size;
  }
  if (total > SKILL_LIMITS.totalBytes) return "The skill is larger than 10 MB in total.";
  return null;
}

/** Multipart body for `POST /api/skills`. Folder files keep their relative path as the filename. */
export function skillFormData(skill: SkillUpload): FormData {
  const fd = new FormData();
  if (skill.kind === "zip") {
    fd.append("file", skill.file, skill.file.name);
  } else {
    for (const p of skill.files) fd.append("files", p.file, p.path);
  }
  return fd;
}

/** Merge knowledge files, dropping duplicates by name+size. */
export function mergeKnowledge(current: File[], added: File[]): File[] {
  const key = (f: File) => `${f.name}:${f.size}`;
  const seen = new Set(current.map(key));
  const out = [...current];
  for (const f of added) {
    if (!seen.has(key(f))) {
      out.push(f);
      seen.add(key(f));
    }
  }
  return out;
}

// ---- DOM helpers (drag-and-drop folder traversal) ----

interface FsEntry {
  isFile: boolean;
  isDirectory: boolean;
  name: string;
  fullPath: string;
}
interface FsFileEntry extends FsEntry {
  file: (ok: (f: File) => void, fail: (e: unknown) => void) => void;
}
interface FsDirEntry extends FsEntry {
  createReader: () => { readEntries: (ok: (entries: FsEntry[]) => void, fail: (e: unknown) => void) => void };
}

async function readDir(dir: FsDirEntry): Promise<FsEntry[]> {
  const reader = dir.createReader();
  const all: FsEntry[] = [];
  // readEntries returns batches until it returns an empty list.
  for (;;) {
    const batch = await new Promise<FsEntry[]>((ok, fail) => reader.readEntries(ok, fail));
    if (batch.length === 0) break;
    all.push(...batch);
    if (all.length > 500) break;
  }
  return all;
}

async function walk(entry: FsEntry, out: PickedFile[]): Promise<void> {
  if (out.length > 500) return;
  if (entry.isFile) {
    const file = await new Promise<File>((ok, fail) => (entry as FsFileEntry).file(ok, fail));
    out.push({ file, path: entry.fullPath.replace(/^\/+/, "") || file.name });
  } else if (entry.isDirectory) {
    for (const child of await readDir(entry as FsDirEntry)) await walk(child, out);
  }
}

/** Files from a drop event, walking dropped folders so relative paths are preserved. */
export async function filesFromDataTransfer(dt: DataTransfer): Promise<PickedFile[]> {
  const items = Array.from(dt.items ?? []);
  const entries = items
    .map((it) => (it.kind === "file" && "webkitGetAsEntry" in it ? (it.webkitGetAsEntry() as FsEntry | null) : null))
    .filter((e): e is FsEntry => e !== null);
  if (entries.length > 0) {
    const out: PickedFile[] = [];
    for (const e of entries) await walk(e, out);
    return out;
  }
  return Array.from(dt.files ?? []).map((file) => ({ file, path: file.name }));
}

/** Files from an `<input type=file>` (with or without `webkitdirectory`). */
export function filesFromInput(list: FileList | null): PickedFile[] {
  return Array.from(list ?? []).map((file) => ({ file, path: file.webkitRelativePath || file.name }));
}
