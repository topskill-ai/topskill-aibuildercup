import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { frame, jsonResponse, mockFetch, sseChunks } from "@/test/http";

vi.mock("@/lib/firebase", () => ({ getIdToken: async () => null }));
vi.mock("@/lib/devUser", () => ({ getDevUser: () => ({ uid: "ana", role: "visitor" }) }));

import AssistantPage from "./AssistantPage";

const META = {
  chat_models: ["gemini-3.8-flash"],
  default_chat_model: "gemini-3.8-flash",
  revenue_share_bp: 8000,
  signup_grant_cents: 10000,
  demo_topup_cents: 5000,
  demo_topup_daily_limit: 5,
  a2a_mandate_defaults: { max_amount_cents: 500, budget_cents: 2000, ttl_minutes: 30, max_budget_cents: 100000, max_ttl_minutes: 1440 },
};

const MANDATE = {
  id: "mdt_abc",
  audience: "*",
  intent: "Our new hire from Brazil needs a work visa plan",
  payer_agent: "enterprise-assistant",
  max_amount_cents: 500,
  budget_cents: 2000,
  spent_cents: 0,
  calls: 0,
  status: "active",
  issued_at: "2026-10-16T08:00:00Z",
  expires_at: "2099-10-16T08:30:00Z",
};

const result = (slug: string, status: string, extra: Record<string, unknown>) => ({
  call_id: null,
  slug,
  status,
  charged_cents: 0,
  creator_share_cents: 0,
  answer_preview: "",
  error_code: null,
  payer_balance_cents: 9600,
  mandate_spent_cents: 0,
  mandate_budget_cents: 2000,
  ...extra,
});

const call = (slug: string, name: string, id: string | null, price: number) => ({
  call_id: id,
  slug,
  agent_name: name,
  endpoint: `http://api/a2a/${slug}`,
  price_cents: price,
  request_preview: `Request for ${name}`,
});

const STREAM = [
  frame("status", { state: "ready", conversation_id: null, turn_id: "t", request_id: "r", model: "gemini-3.8-flash" }),
  frame("tool_start", { id: "f", tool: "find_agents", kind: "search", title: "Searched the agent directory", target: "visa" }),
  frame("a2a_discover", {
    query: "visa, resume, contract",
    agents: [
      { slug: "visa-advisor", name: "Visa Advisor", tagline: "Work visas", price_cents: 200, card_url: "http://c/1" },
      { slug: "resume-reviewer", name: "Resume Reviewer", tagline: "Resumes", price_cents: 200, card_url: "http://c/2" },
      { slug: "contract-reviewer", name: "Contract Reviewer", tagline: "Contracts", price_cents: 600, card_url: "http://c/3" },
    ],
  }),
  frame("tool_end", { id: "f", tool: "find_agents", status: "completed" }),
  frame("tool_start", { id: "c1", tool: "call_agent", kind: "a2a", title: "Called Visa Advisor", target: "visa-advisor" }),
  frame("a2a_call", call("visa-advisor", "Visa Advisor", "id1", 200)),
  frame("a2a_result", result("visa-advisor", "completed", { call_id: "id1", charged_cents: 200, creator_share_cents: 160, answer_preview: "Apply for a VITEM V.", mandate_spent_cents: 200 })),
  frame("tool_end", { id: "c1", tool: "call_agent", status: "completed" }),
  frame("tool_start", { id: "c2", tool: "call_agent", kind: "a2a", title: "Called Resume Reviewer", target: "resume-reviewer" }),
  frame("a2a_call", call("resume-reviewer", "Resume Reviewer", "id2", 200)),
  frame("a2a_result", result("resume-reviewer", "completed", { call_id: "id2", charged_cents: 200, creator_share_cents: 160, mandate_spent_cents: 400 })),
  frame("tool_end", { id: "c2", tool: "call_agent", status: "completed" }),
  frame("tool_start", { id: "c3", tool: "call_agent", kind: "a2a", title: "Called Contract Reviewer", target: "contract-reviewer" }),
  frame("a2a_call", call("contract-reviewer", "Contract Reviewer", null, 600)),
  frame("a2a_result", result("contract-reviewer", "rejected", { error_code: "mandate_exceeds_max", mandate_spent_cents: 400 })),
  frame("tool_end", { id: "c3", tool: "call_agent", status: "error" }),
  frame("text_delta", { delta: "Per Visa Advisor: apply early. The contract check was skipped." }),
  frame("done", {
    conversation_id: null,
    message_id: null,
    turn_id: "t",
    outcome: "ok",
    usage: null,
    billing: null,
    mandate: { id: "mdt_abc", spent_cents: 400, budget_cents: 2000, calls: 2 },
  }),
];

let fetchMock: ReturnType<typeof mockFetch>;
let chatBody: unknown = null;
let mandateBody: unknown = null;

beforeEach(() => {
  chatBody = null;
  mandateBody = null;
  fetchMock = mockFetch();
  fetchMock.mockImplementation(async (input, init) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    if (url.endsWith("/api/public/meta")) return jsonResponse(200, META);
    if (url.endsWith("/api/mandates") && method === "POST") {
      mandateBody = JSON.parse(String(init?.body));
      return jsonResponse(201, { mandate: MANDATE, token: "jws.token.sig" });
    }
    if (url.endsWith("/api/demo/assistant/chat")) {
      chatBody = JSON.parse(String(init?.body));
      return sseChunks(STREAM);
    }
    if (url.includes("/api/wallet/ledger")) return jsonResponse(200, { items: [], next_cursor: null });
    if (url.includes("/api/public/agents/")) return jsonResponse(404, { code: "agent_not_found", message: "x", trace_id: "t", detail: null });
    return jsonResponse(200, {});
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function renderPage() {
  const router = createMemoryRouter([{ path: "/assistant", element: <AssistantPage /> }], { initialEntries: ["/assistant"] });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}

describe("AssistantPage", () => {
  it("authorises a budget, then streams discovery, two paid calls and a rejected call", async () => {
    Element.prototype.scrollIntoView = vi.fn();
    const user = userEvent.setup();
    renderPage();

    await user.click(screen.getByRole("button", { name: /Our new hire from Brazil/ }));

    // The authorise-budget dialog opens first, prefilled from meta defaults.
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByLabelText("Total budget (credits)")).toHaveValue("20.00");
    expect(within(dialog).getByLabelText("Max per call (credits)")).toHaveValue("5.00");
    await user.click(within(dialog).getByTestId("authorise-submit"));

    await waitFor(() => expect(chatBody).not.toBeNull());
    expect(mandateBody).toMatchObject({ audience: "*", max_amount_cents: 500, budget_cents: 2000, ttl_minutes: 30 });
    expect(chatBody).toEqual({
      messages: [{ role: "user", text: expect.stringContaining("Our new hire from Brazil") }],
      mandate_token: "jws.token.sig",
    });

    expect(await screen.findByText("Discovered 3 agents")).toBeInTheDocument();
    const rejection = await screen.findByTestId("call-rejection");
    expect(rejection).toHaveTextContent("Rejected");
    expect(rejection).toHaveTextContent("mandate_exceeds_max");
    expect(rejection).toHaveTextContent("Not charged");
    expect(screen.getAllByText("Verified mandate")).toHaveLength(2);
    expect(screen.getByText("Blocked by mandate")).toBeInTheDocument();
    expect(await screen.findByText(/The contract check was skipped/)).toBeInTheDocument();

    // Mandate card shows the live counters from the stream.
    const panel = screen.getByRole("region", { name: "Payment mandate" });
    expect(within(panel).getByTestId("mandate-spent")).toHaveTextContent("4.00");
    expect(within(panel).getByText("2")).toBeInTheDocument();
  });
});
