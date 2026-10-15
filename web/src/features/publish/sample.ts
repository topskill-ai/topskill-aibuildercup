/**
 * The sample skill served from `public/samples/` so anyone can try the publish wizard without a
 * SKILL.md of their own: a "career-coach" skill folder plus a small coaching guide PDF as knowledge.
 * It is a different skill from the seeded demo agents, so it never shadows them in discovery.
 */
import { baseName, type PickedFile } from "./files";

/** Paths under `samples/`; folder paths become the skill package, the loose PDF becomes knowledge. */
export const SAMPLE_SKILL_PATHS = ["career-coach/SKILL.md", "career-coach/references/interview-questions.md"] as const;
export const SAMPLE_KNOWLEDGE_PATHS = ["career_coaching_guide.pdf"] as const;
export const SAMPLE_SKILL_MD_URL = sampleUrl(SAMPLE_SKILL_PATHS[0]);

export function sampleUrl(path: string, base: string = import.meta.env.BASE_URL ?? "/"): string {
  return `${base.endsWith("/") ? base : `${base}/`}samples/${path}`;
}

const TYPES: Record<string, string> = { ".md": "text/markdown", ".pdf": "application/pdf" };

function typeOf(path: string): string {
  const i = path.lastIndexOf(".");
  return TYPES[i >= 0 ? path.slice(i).toLowerCase() : ""] ?? "application/octet-stream";
}

/** Fetch the sample files as if the creator had dropped the folder and the PDF together. */
export async function loadSampleSkill(fetchImpl: typeof fetch = fetch): Promise<PickedFile[]> {
  const paths = [...SAMPLE_SKILL_PATHS, ...SAMPLE_KNOWLEDGE_PATHS];
  return Promise.all(
    paths.map(async (path) => {
      const res = await fetchImpl(sampleUrl(path));
      // The hosting SPA rewrite answers a missing file with index.html, so check the type too.
      if (!res.ok || (res.headers.get("content-type") ?? "").includes("text/html")) {
        throw new Error("The sample skill could not be loaded. Try again.");
      }
      const blob = await res.blob();
      const file = new File([blob], baseName(path), { type: typeOf(path) });
      return { file, path };
    }),
  );
}
