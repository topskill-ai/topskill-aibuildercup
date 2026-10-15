/** Wire types of the analytics endpoints (SPEC §18.4). Kept here so `api/types.ts` stays untouched. */

export interface AnalyticsStatus {
  enabled: boolean;
  data_as_of: string | null;
  suggestions: string[];
}

export type AnalyticsOutcome = "answered" | "cannot_answer" | "rejected";
export type ChartType = "bar" | "line" | "none";

export interface AnalyticsColumn {
  name: string;
  /** BigQuery type name, e.g. "STRING", "INTEGER", "TIMESTAMP", "DATE", "STRING[]". */
  type: string;
}

export type CellValue = string | number | boolean | null | CellValue[] | { [key: string]: CellValue };

export interface AnalyticsAnswer {
  question: string;
  outcome: AnalyticsOutcome;
  sql: string;
  columns: AnalyticsColumn[];
  rows: CellValue[][];
  answer: string;
  chart_hint: { type: ChartType; x: string | null; y: string | null };
  truncated: boolean;
  bytes_processed: number;
  model: string | null;
  data_as_of: string | null;
}
