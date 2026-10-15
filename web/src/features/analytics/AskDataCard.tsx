/**
 * "Ask your data" (SPEC §18): a creator asks a question in plain English; the backend writes one
 * guarded BigQuery SELECT over their own rows, runs it and summarises the result. Shows the answer,
 * a chart when it fits, the rows and the SQL (collapsible). Hidden when the feature is off.
 */
import { AlertTriangle, ChevronRight, Database, Sparkles } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Button } from "@/components/ui/Button";
import { Card, CardHeader } from "@/components/ui/Card";
import { ErrorLine } from "@/components/ui/ErrorLine";
import { Skeleton } from "@/components/ui/Skeleton";
import { Textarea } from "@/components/ui/Textarea";
import { cn } from "@/lib/cn";
import { formatRelative } from "@/lib/format";
import { useAnalyticsStatus, useAskData } from "./api";
import { chartData, columnLabel, formatBytes, formatCell, isNumericType } from "./format";
import { SimpleChart } from "./SimpleChart";
import type { AnalyticsAnswer } from "./types";

const MAX_TABLE_ROWS = 50;
const MAX_QUESTION = 300;

function ResultTable({ answer }: { answer: AnalyticsAnswer }) {
  const rows = answer.rows.slice(0, MAX_TABLE_ROWS);
  return (
    <div className="max-h-80 overflow-auto rounded-control border border-border">
      <table className="w-full min-w-max border-collapse text-sm">
        <thead className="sticky top-0 bg-slate-50 text-left text-xs font-medium text-muted">
          <tr>
            {answer.columns.map((c) => (
              <th key={c.name} scope="col" className={cn("px-3 py-2", isNumericType(c.type) && "text-right")}>
                {columnLabel(c.name)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {rows.map((row, i) => (
            <tr key={i} data-testid="analytics-row">
              {answer.columns.map((c, j) => (
                <td
                  key={c.name}
                  className={cn("px-3 py-1.5", isNumericType(c.type) && "text-right font-mono tabular-nums")}
                >
                  {formatCell(row[j], c)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {answer.rows.length > MAX_TABLE_ROWS || answer.truncated ? (
        <p className="border-t border-border px-3 py-1.5 text-xs text-subtle">
          Showing the first {Math.min(rows.length, MAX_TABLE_ROWS)} rows.
        </p>
      ) : null}
    </div>
  );
}

function AnswerView({ answer }: { answer: AnalyticsAnswer }) {
  const chart = chartData(answer);
  const answered = answer.outcome === "answered";
  return (
    <div className="space-y-4" data-testid="analytics-answer">
      <div className={cn("flex items-start gap-2.5", !answered && "text-muted")}>
        {answered ? (
          <Sparkles aria-hidden className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
        ) : (
          <AlertTriangle aria-hidden className="mt-0.5 h-4 w-4 shrink-0 text-warning" />
        )}
        <p className="min-w-0 text-[15px] leading-6 break-words">{answer.answer}</p>
      </div>
      {chart ? <SimpleChart data={chart} /> : null}
      {answered && answer.columns.length > 0 && answer.rows.length > 0 ? <ResultTable answer={answer} /> : null}
      {answer.sql ? (
        <details className="group rounded-control border border-border">
          <summary className="flex cursor-pointer list-none items-center gap-1.5 px-3 py-2 text-sm text-muted select-none hover:text-text">
            <ChevronRight aria-hidden className="h-4 w-4 transition-transform group-open:rotate-90" />
            Show SQL
          </summary>
          <pre className="overflow-x-auto border-t border-border bg-slate-50 px-3 py-2 font-mono text-xs leading-5 whitespace-pre-wrap break-words text-text">
            {answer.sql}
          </pre>
        </details>
      ) : null}
      <p className="text-xs text-subtle">
        {answer.data_as_of ? `Data as of ${formatRelative(answer.data_as_of)}` : "Data freshness unknown"}
        {answered ? ` · scanned ${formatBytes(answer.bytes_processed)} in BigQuery` : ""}
        {answer.model ? ` · ${answer.model}` : ""}
      </p>
    </div>
  );
}

export function AskDataCard({ className }: { className?: string }) {
  const status = useAnalyticsStatus();
  const ask = useAskData();
  const [question, setQuestion] = useState("");

  if (!status.data?.enabled) return null; // feature off, still loading, or backend without analytics

  const submit = (q: string) => {
    const text = q.trim();
    if (text.length < 3 || ask.isPending) return;
    setQuestion(text);
    ask.mutate(text);
  };
  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    submit(question);
  };

  return (
    <Card className={className} data-testid="ask-data-card">
      <CardHeader
        title={
          <span className="inline-flex items-center gap-2">
            <Database aria-hidden className="h-4 w-4 text-primary" />
            Ask your data
          </span>
        }
        description="Questions about your earnings, rounds, A2A calls and records, answered from BigQuery."
      />
      <div className="space-y-4 p-5">
        <form onSubmit={onSubmit} className="flex flex-col gap-2 sm:flex-row sm:items-start">
          <Textarea
            aria-label="Your question"
            className="min-w-0 flex-1"
            rows={1}
            autoResize
            maxRows={4}
            maxLength={MAX_QUESTION}
            placeholder="e.g. Which agent earned the most from A2A this week?"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                submit(question);
              }
            }}
          />
          <Button type="submit" loading={ask.isPending} disabled={question.trim().length < 3}>
            Ask
          </Button>
        </form>
        <div className="flex flex-wrap gap-2" aria-label="Suggested questions">
          {status.data.suggestions.map((s) => (
            <button
              key={s}
              type="button"
              disabled={ask.isPending}
              onClick={() => submit(s)}
              className="max-w-full rounded-chip border border-border bg-surface px-3 py-1 text-left text-xs text-muted transition-colors duration-150 hover:border-border-strong hover:text-text disabled:opacity-50"
            >
              {s}
            </button>
          ))}
        </div>
        {ask.isPending ? (
          <div className="space-y-2" aria-live="polite" aria-busy="true">
            <p className="text-sm text-muted">Writing a query and running it on BigQuery…</p>
            <Skeleton className="h-4 w-3/4" />
            <Skeleton className="h-32 w-full" />
          </div>
        ) : ask.error ? (
          <ErrorLine error={ask.error} onRetry={() => submit(question)} />
        ) : ask.data ? (
          <AnswerView answer={ask.data} />
        ) : null}
      </div>
    </Card>
  );
}
