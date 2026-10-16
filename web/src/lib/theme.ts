import { useSyncExternalStore } from "react";
import { safeLocal } from "./safeStorage";

/**
 * Light / dark theme (SPEC §11.3). The theme is the `data-theme` attribute on `<html>`; every colour
 * token in styles.css is overridden under `:root[data-theme="dark"]`.
 *
 * The first paint is handled by the inline `#theme-init` script in index.html, which mirrors
 * `resolveInitialTheme` (it cannot import this module before the bundle loads). Keep the two in sync —
 * `theme.test.ts` runs the inline script against the same cases.
 */
export type Theme = "light" | "dark";

export const THEME_STORAGE_KEY = "topskill-theme";
const THEME_QUERY_PARAM = "theme";

/** `<meta name="theme-color">` per theme (the page background, `--color-bg`); index.html uses the same values. */
export const THEME_COLORS: Record<Theme, string> = { light: "#F7F8FB", dark: "#0E0F1E" };

function isTheme(value: unknown): value is Theme {
  return value === "light" || value === "dark";
}

/** Initial theme: `?theme=` → stored choice → system preference → light. Invalid values are skipped. */
export function resolveInitialTheme(opts: { search?: string; stored?: string | null; prefersDark?: boolean }): Theme {
  const fromQuery = new URLSearchParams(opts.search ?? "").get(THEME_QUERY_PARAM);
  if (isTheme(fromQuery)) return fromQuery;
  if (isTheme(opts.stored)) return opts.stored;
  return opts.prefersDark ? "dark" : "light";
}

function root(): HTMLElement | null {
  return typeof document === "undefined" ? null : document.documentElement;
}

/** The theme currently applied to `<html>` (light when nothing is set). */
export function getTheme(): Theme {
  const value = root()?.getAttribute("data-theme");
  return value === "dark" ? "dark" : "light";
}

const listeners = new Set<() => void>();

/** Apply a theme (data-theme + browser theme-color) and remember it for later visits (localStorage `topskill-theme`). */
export function setTheme(theme: Theme): void {
  const el = root();
  el?.setAttribute("data-theme", theme);
  el?.ownerDocument.querySelector('meta[name="theme-color"]')?.setAttribute("content", THEME_COLORS[theme]);
  safeLocal.set(THEME_STORAGE_KEY, theme);
  listeners.forEach((l) => l());
}

export function toggleTheme(): Theme {
  const next: Theme = getTheme() === "dark" ? "light" : "dark";
  setTheme(next);
  return next;
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** Current theme plus a toggle; re-renders when the theme changes anywhere in the app. */
export function useTheme(): { theme: Theme; toggle: () => void } {
  const theme = useSyncExternalStore(subscribe, getTheme, () => "light" as Theme);
  return { theme, toggle: toggleTheme };
}
