import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Agent } from "@/api/types";
import { jsonResponse, mockFetch } from "@/test/http";

vi.mock("@/lib/firebase", () => ({ getIdToken: async () => null }));
vi.mock("@/lib/devUser", () => ({ getDevUser: () => ({ uid: "maya", role: "creator" }) }));
vi.mock("@/lib/toast", () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() }, toastApiError: vi.fn() }));

import { SafetySection } from "./SafetySection";

const agent = {
  id: "agt_1",
  slug: "visa-advisor",
  title: "Visa Advisor",
  config: {
    soul: "s",
    model: "gemini-3.8-flash",
    temperature: null,
    web_search_enabled: true,
    skill_ids: ["skl_1"],
    record_schema: null,
    safety_screening: true,
  },
  stats: {
    rounds: 0,
    paid_rounds: 0,
    conversations: 0,
    visitor_gross_cents: 0,
    creator_earned_cents: 0,
    a2a_calls: 0,
    a2a_gross_cents: 0,
    records: 0,
    last_activity_at: null,
    guard: {
      prompts_screened: 12,
      prompts_blocked: 3,
      prompts_redacted: 1,
      answers_screened: 9,
      answers_blocked: 0,
      answers_redacted: 2,
      answers_flagged: 1,
      failed_open: 0,
    },
  },
} as unknown as Agent;

afterEach(() => vi.unstubAllGlobals());

describe("SafetySection", () => {
  it("shows the counters and saves the switch live", async () => {
    const fetchMock = mockFetch();
    fetchMock.mockResolvedValueOnce(
      jsonResponse(200, { ...agent, config: { ...agent.config, safety_screening: false } }),
    );
    render(
      <QueryClientProvider client={new QueryClient()}>
        <SafetySection agent={agent} />
      </QueryClientProvider>,
    );
    const counters = screen.getByTestId("guard-counters");
    expect(counters).toHaveTextContent("Messages blocked3");
    expect(counters).toHaveTextContent("Answers redacted2");
    expect(counters).toHaveTextContent("Answers withheld0");
    expect(screen.queryByTestId("guard-unavailable")).toBeNull();

    const toggle = screen.getByRole("switch", { name: /Safety screening \(Model Armor\)/ });
    expect(toggle).toHaveAttribute("aria-checked", "true");
    await userEvent.click(toggle);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toMatch(/\/api\/agents\/agt_1$/);
    expect(init?.method).toBe("PATCH");
    expect(JSON.parse(String(init?.body))).toEqual({ config: { safety_screening: false } });
  });

  it("warns when screening is on but not running on this deployment", () => {
    render(
      <QueryClientProvider client={new QueryClient()}>
        <SafetySection agent={{ ...agent, guard_active: false }} />
      </QueryClientProvider>,
    );
    expect(screen.getByTestId("guard-unavailable")).toHaveTextContent(/unavailable on this deployment/);
  });
});
