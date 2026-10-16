import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import indexHtml from "../../index.html?raw";
import {
  getTheme,
  resolveInitialTheme,
  setTheme,
  THEME_COLORS,
  THEME_STORAGE_KEY,
  toggleTheme,
  useTheme,
} from "./theme";

type Case = { name: string; search: string; stored: string | null; prefersDark: boolean; expected: "light" | "dark" };

const CASES: Case[] = [
  { name: "query wins over storage and system", search: "?theme=light", stored: "dark", prefersDark: true, expected: "light" },
  { name: "query dark", search: "?theme=dark", stored: "light", prefersDark: false, expected: "dark" },
  { name: "storage wins over system", search: "", stored: "light", prefersDark: true, expected: "light" },
  { name: "stored dark", search: "", stored: "dark", prefersDark: false, expected: "dark" },
  { name: "system dark when nothing stored", search: "", stored: null, prefersDark: true, expected: "dark" },
  { name: "light by default", search: "", stored: null, prefersDark: false, expected: "light" },
  { name: "invalid query falls through to storage", search: "?theme=neon", stored: "dark", prefersDark: false, expected: "dark" },
  { name: "invalid storage falls through to system", search: "", stored: "sepia", prefersDark: true, expected: "dark" },
];

function stubMatchMedia(prefersDark: boolean) {
  vi.stubGlobal(
    "matchMedia",
    vi.fn((query: string) => ({ matches: prefersDark && query.includes("dark"), media: query })),
  );
}

/** The inline first-paint script from index.html. */
function inlineThemeScript(): string {
  const match = /<script id="theme-init">([\s\S]*?)<\/script>/.exec(indexHtml);
  if (!match) throw new Error("index.html has no <script id=\"theme-init\">");
  return match[1];
}

/** The single `<meta name="theme-color">` from index.html, recreated in the test document. */
function themeColorMeta(): HTMLMetaElement {
  return document.head.querySelector('meta[name="theme-color"]') as HTMLMetaElement;
}

beforeEach(() => {
  document.documentElement.removeAttribute("data-theme");
  localStorage.clear();
  const tag = /<meta name="theme-color"[^>]*>/.exec(indexHtml);
  if (!tag) throw new Error('index.html has no <meta name="theme-color">');
  document.head.querySelectorAll('meta[name="theme-color"]').forEach((m) => m.remove());
  document.head.insertAdjacentHTML("beforeend", tag[0]);
});

afterEach(() => {
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

describe("resolveInitialTheme", () => {
  it.each(CASES)("$name", ({ search, stored, prefersDark, expected }) => {
    expect(resolveInitialTheme({ search, stored, prefersDark })).toBe(expected);
  });
});

describe("index.html first-paint script", () => {
  it.each(CASES)("$name (sets data-theme like resolveInitialTheme)", ({ search, stored, prefersDark, expected }) => {
    window.history.replaceState(null, "", `/${search}`);
    if (stored !== null) localStorage.setItem(THEME_STORAGE_KEY, stored);
    stubMatchMedia(prefersDark);

    new Function(inlineThemeScript())();

    expect(document.documentElement.getAttribute("data-theme")).toBe(expected);
    expect(themeColorMeta().getAttribute("content")).toBe(THEME_COLORS[expected]);
  });

  it("ships one theme-color meta (no media variants that would follow the system instead of data-theme)", () => {
    expect(indexHtml.match(/<meta name="theme-color"/g)).toHaveLength(1);
    expect(themeColorMeta().getAttribute("content")).toBe(THEME_COLORS.light);
  });

  it("falls back to light when storage and matchMedia throw", () => {
    vi.stubGlobal("matchMedia", () => {
      throw new Error("unsupported");
    });
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    try {
      new Function(inlineThemeScript())();
    } finally {
      vi.restoreAllMocks();
    }
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
  });
});

describe("setTheme / toggleTheme", () => {
  it("sets data-theme on <html> and remembers the choice", () => {
    setTheme("dark");
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe("dark");
    expect(getTheme()).toBe("dark");
    expect(themeColorMeta().getAttribute("content")).toBe(THEME_COLORS.dark);
    setTheme("light");
    expect(themeColorMeta().getAttribute("content")).toBe(THEME_COLORS.light);
  });

  it("toggles between light and dark", () => {
    document.documentElement.setAttribute("data-theme", "light");
    expect(toggleTheme()).toBe("dark");
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
    expect(toggleTheme()).toBe("light");
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe("light");
  });

  it("useTheme re-renders when the theme changes", () => {
    document.documentElement.setAttribute("data-theme", "light");
    const { result } = renderHook(() => useTheme());
    expect(result.current.theme).toBe("light");
    act(() => result.current.toggle());
    expect(result.current.theme).toBe("dark");
    act(() => setTheme("light"));
    expect(result.current.theme).toBe("light");
  });
});
