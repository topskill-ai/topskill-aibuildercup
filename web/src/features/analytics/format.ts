/** Pure helpers for "Ask your data" results: cell formatting, column labels and chart data. */
import { formatDate, formatDateTime } from "@/lib/format";
import { formatCredits } from "@/lib/money";
import type { AnalyticsAnswer, AnalyticsColumn, CellValue, ChartType } from "./types";

const NUMERIC = new Set(["INTEGER", "INT64", "FLOAT", "FLOAT64", "NUMERIC", "BIGNUMERIC"]);
const MAX_CHART_POINTS = 60;

export function isNumericType(type: string): boolean {
  return NUMERIC.has(type.toUpperCase());
}

/** Money columns follow the backend convention: integer cents in a column named `*_cents`. */
export function isMoneyColumn(column: AnalyticsColumn): boolean {
  return column.name.endsWith("_cents") && isNumericType(column.type);
}

/** `income_cents` → `Income (credits)`, `a2a_calls` → `A2A calls`. */
export function columnLabel(name: string): string {
  const money = name.endsWith("_cents");
  const base = (money ? name.slice(0, -"_cents".length) : name)
    .split("_")
    .filter(Boolean)
    .map((w) => (/^(a2a|id|sql|ai|cv|url)$/i.test(w) ? w.toUpperCase() : w))
    .join(" ");
  const label = base ? base.charAt(0).toUpperCase() + base.slice(1) : name;
  return money ? `${label} (credits)` : label;
}

export function formatCell(value: CellValue, column: AnalyticsColumn): string {
  if (value === null || value === undefined) return "—";
  if (Array.isArray(value)) return value.length ? value.map((v) => formatCell(v, { ...column, type: "STRING" })).join(", ") : "—";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "number") {
    if (isMoneyColumn(column)) return formatCredits(value);
    return Number.isInteger(value) ? value.toLocaleString("en-US") : value.toLocaleString("en-US", { maximumFractionDigits: 2 });
  }
  if (typeof value === "object") return JSON.stringify(value);
  const type = column.type.toUpperCase();
  if (type === "TIMESTAMP" || type === "DATETIME") return formatDateTime(value);
  if (type === "DATE") return formatDate(`${value}T00:00:00Z`);
  return value;
}

export interface ChartPoint {
  label: string;
  value: number;
}

export interface ChartData {
  type: Exclude<ChartType, "none">;
  xLabel: string;
  yLabel: string;
  money: boolean;
  points: ChartPoint[];
}

/** Chart series from the answer, or `null` when the hint does not fit the rows. */
export function chartData(answer: AnalyticsAnswer): ChartData | null {
  const hint = answer.chart_hint;
  if (hint.type === "none" || !hint.x || !hint.y) return null;
  const xi = answer.columns.findIndex((c) => c.name === hint.x);
  const yi = answer.columns.findIndex((c) => c.name === hint.y);
  if (xi < 0 || yi < 0 || xi === yi) return null;
  const yCol = answer.columns[yi];
  if (!isNumericType(yCol.type)) return null;
  if (answer.rows.length < 2 || answer.rows.length > MAX_CHART_POINTS) return null;
  const xCol = answer.columns[xi];
  const points: ChartPoint[] = [];
  for (const row of answer.rows) {
    const y = row[yi];
    if (typeof y !== "number" || !Number.isFinite(y)) continue;
    points.push({ label: formatCell(row[xi], xCol), value: y });
  }
  if (points.length < 2) return null;
  return {
    type: hint.type,
    xLabel: columnLabel(xCol.name),
    yLabel: columnLabel(yCol.name),
    money: isMoneyColumn(yCol),
    points,
  };
}

/** "1.2 MB" style size for the "scanned" note. */
export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** Up to 4 round axis ticks from 0 to a "nice" max ≥ the data max. */
export function niceTicks(max: number): number[] {
  if (!(max > 0)) return [0, 1];
  const rough = max / 3;
  const pow = 10 ** Math.floor(Math.log10(rough));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * pow).find((s) => s >= rough) ?? 10 * pow;
  const ticks: number[] = [];
  for (let v = 0; v < max + step; v += step) {
    ticks.push(Math.round(v * 1e6) / 1e6);
    if (v >= max) break;
  }
  return ticks;
}
