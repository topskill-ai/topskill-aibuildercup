// @vitest-environment node
/// <reference types="node" />
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

/*
 * WCAG contrast guard for the colour tokens text is drawn with (SPEC §11.3): status / accent `*-text` on
 * their soft tints and on the page surfaces, and the Wallet balance card (white text and the `inverse`
 * button on the brand gradient), in both themes. Values are read from styles.css, so retuning a token
 * below 4.5:1 fails here.
 * (Read from disk: vitest runs with `css: false`, which empties `?raw` CSS imports.)
 */
const css = readFileSync(new URL("./styles.css", import.meta.url), "utf8");

type Rgb = [number, number, number];
type Rgba = [number, number, number, number];

function block(selector: string): string {
  const start = css.indexOf(selector);
  if (start < 0) throw new Error(`missing ${selector}`);
  const open = css.indexOf("{", start);
  const close = css.indexOf("}", open);
  return css.slice(open + 1, close);
}

function vars(body: string): Map<string, string> {
  const out = new Map<string, string>();
  for (const m of body.matchAll(/--color-([\w-]+):\s*([^;]+);/g)) out.set(m[1], m[2].trim());
  return out;
}

const LIGHT = vars(block("@theme {"));
const DARK = new Map([...LIGHT, ...vars(block(':root[data-theme="dark"] {\n    color-scheme: dark;\n    --color-bg'))]);

function parse(value: string, theme: Map<string, string>): Rgba {
  const ref = /^var\(--color-([\w-]+)\)$/.exec(value);
  if (ref) return parse(theme.get(ref[1]) ?? "", theme);
  const hex = /^#([0-9a-f]{6})$/i.exec(value);
  if (hex) return [0, 2, 4].map((i) => parseInt(hex[1].slice(i, i + 2), 16)).concat(1) as Rgba;
  const rgba = /^rgba?\(([^)]+)\)$/.exec(value);
  if (rgba) {
    const [r, g, b, a = "1"] = rgba[1].split(",").map((p) => p.trim());
    return [Number(r), Number(g), Number(b), Number(a)];
  }
  throw new Error(`unparsed colour ${value}`);
}

const over = (fg: Rgba, bg: Rgb): Rgb => [0, 1, 2].map((i) => fg[i] * fg[3] + bg[i] * (1 - fg[3])) as Rgb;

function luminance(c: Rgb): number {
  const [r, g, b] = c.map((v) => {
    const s = v / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function ratio(a: Rgb, b: Rgb): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

const THEMES = { light: LIGHT, dark: DARK };
const TONES = ["success", "warning", "danger", "accent-amber", "accent-blue", "accent-emerald", "accent-rose", "accent-violet"];

describe.each(Object.entries(THEMES))("%s theme text contrast", (_name, theme) => {
  const solid = (token: string): Rgb => over(parse(theme.get(token) ?? "", theme), [0, 0, 0]);
  const surface = solid("surface");

  it.each(TONES)("%s-text is >= 4.5:1 on its soft tint and the page surfaces", (tone) => {
    const text = solid(`${tone}-text`);
    const backgrounds: Rgb[] = [
      over(parse(theme.get(`${tone}-soft`) ?? "", theme), surface),
      ...["surface", "bg", "sunken", "mist"].map((t) => over(parse(theme.get(t) ?? "", theme), surface)),
    ];
    for (const bg of backgrounds) expect(ratio(text, bg)).toBeGreaterThanOrEqual(4.5);
  });

  it("balance card text and the inverse button read >= 4.5:1 on the brand gradient", () => {
    const fg = parse(theme.get("primary-fg") ?? "", theme);
    for (const stop of [solid("brand-from"), solid("brand-to")]) {
      // White glow blob (bg-primary-fg/10) that drifts over the right edge of the card.
      for (const bg of [stop, over([...fg.slice(0, 3), 0.1] as Rgba, stop)]) {
        expect(ratio(over(fg, bg), bg)).toBeGreaterThanOrEqual(4.5);
        // Button.tsx `inverse`: bg-primary-fg/90 + text-brand-from.
        const button = over([...fg.slice(0, 3), 0.9] as Rgba, bg);
        expect(ratio(solid("brand-from"), button)).toBeGreaterThanOrEqual(4.5);
      }
    }
  });
});
