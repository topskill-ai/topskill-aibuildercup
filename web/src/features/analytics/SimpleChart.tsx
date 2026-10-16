/**
 * One-series bar or line chart for "Ask your data" (single hue = primary, so no legend; the title
 * names the series). Thin marks with rounded data ends, recessive grid, a hover/focus tooltip per
 * point, and the full table under it as the accessible view.
 */
import { useEffect, useRef, useState, type RefObject } from "react";
import { formatCredits } from "@/lib/money";
import { cn } from "@/lib/cn";
import { niceTicks, type ChartData } from "./format";

const DEFAULT_W = 640;
const H = 240;
const PAD = { top: 12, right: 12, bottom: 34, left: 56 };

/** Pixel width of the element (SVG drawn 1:1 so text stays 11px at every width). */
function useWidth(ref: RefObject<HTMLElement | null>): number {
  const [width, setWidth] = useState(DEFAULT_W);
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver((entries) => {
      const w = Math.round(entries[0]?.contentRect.width ?? 0);
      if (w > 0) setWidth(w);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [ref]);
  return width;
}

function formatValue(v: number, money: boolean): string {
  if (money) return formatCredits(v);
  return Number.isInteger(v) ? v.toLocaleString("en-US") : v.toLocaleString("en-US", { maximumFractionDigits: 2 });
}

function shortLabel(label: string, max = 14): string {
  return label.length > max ? `${label.slice(0, max - 1)}…` : label;
}

export function SimpleChart({ data, className }: { data: ChartData; className?: string }) {
  const [active, setActive] = useState<number | null>(null);
  const figRef = useRef<HTMLElement | null>(null);
  const W = Math.max(280, useWidth(figRef));
  const values = data.points.map((p) => p.value);
  const minV = Math.min(0, ...values);
  const ticks = niceTicks(Math.max(...values, 0));
  const top = ticks[ticks.length - 1];
  const plotW = W - PAD.left - PAD.right;
  const plotH = H - PAD.top - PAD.bottom;
  const y = (v: number) => PAD.top + plotH - ((v - minV) / (top - minV || 1)) * plotH;
  const n = data.points.length;
  const band = plotW / n;
  const cx = (i: number) => PAD.left + band * i + band / 2;
  const labelEvery = Math.max(1, Math.ceil(n / Math.max(2, Math.floor(plotW / 90))));
  const barW = Math.max(4, Math.min(40, band - 6));
  const base = y(0);

  const linePath = data.points.map((p, i) => `${i === 0 ? "M" : "L"}${cx(i).toFixed(1)},${y(p.value).toFixed(1)}`).join(" ");
  const activePoint = active === null ? null : data.points[active];

  return (
    <figure ref={figRef} className={cn("relative min-w-0", className)}>
      <svg
        width={W}
        height={H}
        viewBox={`0 0 ${W} ${H}`}
        className="block max-w-full"
        role="img"
        aria-label={`${data.type === "line" ? "Line" : "Bar"} chart of ${data.yLabel} by ${data.xLabel}`}
        onMouseLeave={() => setActive(null)}
      >
        {ticks.map((t) => (
          <g key={t}>
            <line x1={PAD.left} x2={W - PAD.right} y1={y(t)} y2={y(t)} stroke="var(--color-border)" strokeWidth={1} />
            <text x={PAD.left - 8} y={y(t)} dy="0.32em" textAnchor="end" fontSize={11} fill="var(--color-subtle)">
              {formatValue(t, data.money)}
            </text>
          </g>
        ))}
        {data.points.map((p, i) =>
          i % labelEvery === 0 ? (
            <text key={`x${i}`} x={cx(i)} y={H - PAD.bottom + 18} textAnchor="middle" fontSize={11} fill="var(--color-muted)">
              {shortLabel(p.label, Math.max(6, Math.floor((band * labelEvery) / 7)))}
            </text>
          ) : null,
        )}
        {data.type === "bar"
          ? data.points.map((p, i) => {
              const yv = y(p.value);
              const h = Math.max(0, base - yv);
              const x = cx(i) - barW / 2;
              const r = Math.min(4, h, barW / 2);
              // Rounded data end only (anchored flat on the baseline).
              const d = `M${x},${base} V${yv + r} Q${x},${yv} ${x + r},${yv} H${x + barW - r} Q${x + barW},${yv} ${x + barW},${yv + r} V${base} Z`;
              return (
                <path
                  key={`b${i}`}
                  d={d}
                  fill="var(--color-primary)"
                  opacity={active === null || active === i ? 1 : 0.55}
                  data-testid="chart-bar"
                />
              );
            })
          : (
            <>
              <path d={linePath} fill="none" stroke="var(--color-primary)" strokeWidth={2} strokeLinejoin="round" />
              {data.points.map((p, i) => (
                <circle
                  key={`p${i}`}
                  cx={cx(i)}
                  cy={y(p.value)}
                  r={active === i ? 5 : 4}
                  fill="var(--color-primary)"
                  stroke="var(--color-surface)"
                  strokeWidth={2}
                  data-testid="chart-point"
                />
              ))}
            </>
          )}
        <line x1={PAD.left} x2={W - PAD.right} y1={base} y2={base} stroke="var(--color-border-strong)" strokeWidth={1} />
        {data.points.map((p, i) => (
          <rect
            key={`h${i}`}
            x={PAD.left + band * i}
            y={PAD.top}
            width={band}
            height={plotH}
            fill="transparent"
            tabIndex={0}
            aria-label={`${p.label}: ${formatValue(p.value, data.money)}`}
            onMouseEnter={() => setActive(i)}
            onFocus={() => setActive(i)}
            onBlur={() => setActive(null)}
            className="outline-none"
          />
        ))}
      </svg>
      {activePoint && active !== null ? (
        <div
          role="status"
          className="pointer-events-none absolute top-1 rounded-control border border-border bg-surface px-2.5 py-1.5 text-xs shadow-pop"
          style={{ left: `${(cx(active) / W) * 100}%`, transform: "translateX(-50%)" }}
        >
          <div className="text-muted">{activePoint.label}</div>
          <div className="font-mono font-medium text-text">
            {formatValue(activePoint.value, data.money)}
            {data.money ? " credits" : ""}
          </div>
        </div>
      ) : null}
      <figcaption className="mt-1 text-center text-xs text-subtle">
        {data.yLabel} by {data.xLabel}
      </figcaption>
    </figure>
  );
}
