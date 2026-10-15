/**
 * Web Storage accessors that never throw (private windows, blocked site data, SSR/tests).
 * Use for per-viewer conveniences only (SPEC §11.4); never for state that must persist.
 */

type Area = "local" | "session";

function area(kind: Area): Storage | null {
  try {
    const s = kind === "local" ? globalThis.localStorage : globalThis.sessionStorage;
    return s ?? null;
  } catch {
    return null;
  }
}

function makeStore(kind: Area) {
  return {
    get(key: string): string | null {
      try {
        return area(kind)?.getItem(key) ?? null;
      } catch {
        return null;
      }
    },
    set(key: string, value: string): void {
      try {
        area(kind)?.setItem(key, value);
      } catch {
        /* storage unavailable or full: ignore */
      }
    },
    remove(key: string): void {
      try {
        area(kind)?.removeItem(key);
      } catch {
        /* ignore */
      }
    },
    getJSON<T>(key: string): T | null {
      const raw = this.get(key);
      if (raw === null) return null;
      try {
        return JSON.parse(raw) as T;
      } catch {
        return null;
      }
    },
    setJSON(key: string, value: unknown): void {
      try {
        this.set(key, JSON.stringify(value));
      } catch {
        /* unserialisable: ignore */
      }
    },
  };
}

export const safeLocal = makeStore("local");
export const safeSession = makeStore("session");
