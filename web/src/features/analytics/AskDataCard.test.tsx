import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { jsonResponse, mockFetch } from "@/test/http";
import { AskDataCard } from "./AskDataCard";
import type { AnalyticsAnswer, AnalyticsStatus } from "./types";

vi.mock("@/lib/firebase", () => ({ getIdToken: async () => null }));
vi.mock("@/lib/devUser", () => ({ getDevUser: () => null }));

const status: AnalyticsStatus = {
  enabled: true,
  data_as_of: "2026-10-16T08:30:00Z",
  suggestions: ["Which agent earned the most from A2A this week?", "How much did I earn per day?"],
};

const answer: AnalyticsAnswer = {
  question: "Which agent earned the most from A2A this week?",
  outcome: "answered",
  sql: "SELECT agent_title, SUM(amount_cents) AS income_cents FROM ledger GROUP BY 1",
  columns: [
    { name: "agent_title", type: "STRING" },
    { name: "income_cents", type: "INTEGER" },
  ],
  rows: [
    ["Visa Advisor", 600],
    ["Resume Reviewer", 160],
  ],
  answer: "Visa Advisor earned the most from A2A this week: 6.00 credits.",
  chart_hint: { type: "bar", x: "agent_title", y: "income_cents" },
  truncated: false,
  bytes_processed: 305,
  model: "gemini-3.8-flash",
  data_as_of: "2026-10-16T08:30:00Z",
};

function renderCard() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <AskDataCard />
    </QueryClientProvider>,
  );
}

let fetchMock: ReturnType<typeof mockFetch>;

beforeEach(() => {
  fetchMock = mockFetch();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("AskDataCard", () => {
  it("asks a suggested question and shows answer, chart, table and SQL", async () => {
    fetchMock.mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.endsWith("/api/analytics/status")) return jsonResponse(200, status);
      if (url.endsWith("/api/analytics/ask")) {
        expect(JSON.parse(String(init?.body))).toEqual({ question: status.suggestions[0] });
        return jsonResponse(200, answer);
      }
      return jsonResponse(404, { code: "not_found", message: "x", trace_id: "t", detail: null });
    });
    renderCard();
    const chip = await screen.findByRole("button", { name: status.suggestions[0] });
    await userEvent.click(chip);
    expect(await screen.findByText(answer.answer)).toBeInTheDocument();
    expect(screen.getAllByTestId("chart-bar")).toHaveLength(2);
    expect(screen.getAllByTestId("analytics-row")).toHaveLength(2);
    expect(screen.getByText("Income (credits)")).toBeInTheDocument();
    expect(within(screen.getAllByTestId("analytics-row")[0]).getByText("6.00")).toBeInTheDocument();
    expect(screen.getByText("Show SQL")).toBeInTheDocument();
    expect(screen.getByText(answer.sql)).toBeInTheDocument();
  });

  it("shows the error with its trace id when asking fails", async () => {
    fetchMock.mockImplementation(async (input) => {
      const url = String(input);
      if (url.endsWith("/api/analytics/status")) return jsonResponse(200, status);
      return jsonResponse(429, {
        code: "rate_limited",
        message: "Too many requests. Please try again later.",
        trace_id: "trace-123",
        detail: { retry_after_s: 60 },
      });
    });
    renderCard();
    const box = await screen.findByLabelText("Your question");
    await userEvent.type(box, "How much did I earn?{Enter}");
    expect(await screen.findByText("Too many requests. Please try again later.")).toBeInTheDocument();
    expect(screen.getByTestId("trace-id")).toHaveTextContent("trace-123");
  });

  it("stays hidden when the feature is off or the backend has no analytics", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, { ...status, enabled: false }));
    const { container, unmount } = renderCard();
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(container).toBeEmptyDOMElement();
    unmount();

    fetchMock.mockResolvedValueOnce(jsonResponse(404, { code: "not_found", message: "Not found.", trace_id: "t", detail: null }));
    const second = renderCard();
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(second.container).toBeEmptyDOMElement();
  });
});
