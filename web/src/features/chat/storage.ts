/**
 * Per-tab conversation persistence (SPEC §11.4): the conversation id lives in sessionStorage key
 * `topskill.chat.<slug>`, a transcript stash in `topskill.chat.<slug>.<cid>`. All access goes
 * through `safeSession`, so a blocked storage silently degrades to "no restore".
 */
import type { ChatMessage } from "@/api/types";
import { safeSession } from "@/lib/safeStorage";

export const conversationKey = (slug: string) => `topskill.chat.${slug}`;
export const transcriptKey = (slug: string, cid: string) => `topskill.chat.${slug}.${cid}`;

/** Stash payload; `uid` guards against showing another account's transcript in the same tab. */
export interface TranscriptStash {
  uid: string;
  messages: ChatMessage[];
}

const MAX_STASH_MESSAGES = 60;

export function readConversationId(slug: string): string | null {
  const v = safeSession.get(conversationKey(slug));
  return v && /^[A-Za-z0-9_-]{1,80}$/.test(v) ? v : null;
}

export function writeConversationId(slug: string, cid: string): void {
  safeSession.set(conversationKey(slug), cid);
}

export function readTranscript(slug: string, cid: string, uid: string): ChatMessage[] | null {
  const stash = safeSession.getJSON<TranscriptStash>(transcriptKey(slug, cid));
  if (!stash || stash.uid !== uid || !Array.isArray(stash.messages)) return null;
  return stash.messages;
}

export function writeTranscript(slug: string, cid: string, uid: string, messages: ChatMessage[]): void {
  safeSession.setJSON(transcriptKey(slug, cid), { uid, messages: messages.slice(-MAX_STASH_MESSAGES) });
}

/** Forget the stored conversation of this agent (and its stash). */
export function clearConversation(slug: string): void {
  const cid = readConversationId(slug);
  if (cid) safeSession.remove(transcriptKey(slug, cid));
  safeSession.remove(conversationKey(slug));
}
