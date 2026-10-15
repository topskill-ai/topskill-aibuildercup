import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { BillingStatus, ChatMessage, ConversationOut, Me, Meta, PublicAgent } from "@/api/types";
import { frame, jsonResponse, mockFetch, sseChunks } from "@/test/http";

vi.mock("@/lib/firebase", () => ({ getIdToken: async () => null }));
vi.mock("@/lib/devUser", () => ({ getDevUser: () => null }));

const me: Me = {
  uid: "visitor-1",
  email: "ana@example.com",
  display_name: "Ana",
  photo_url: null,
  role: "visitor",
  creator_profile: null,
  created_at: "2026-10-16T08:30:00Z",
};
vi.mock("@/lib/auth", () => ({ useAuth: () => ({ status: "signed_in", me }) }));
const ensureSignedIn = vi.fn(async () => true);
vi.mock("@/components/LoginDialog", () => ({ useLoginDialog: () => ({ ensureSignedIn }) }));

import { AgentChat } from "./AgentChat";
import { conversationKey, transcriptKey } from "./storage";

const agent: PublicAgent = {
  slug: "visa-advisor",
  title: "Visa Advisor",
  tagline: "Work visa plans with cited sources",
  avatar_url: null,
  accent_color: "blue",
  creator: { uid: "creator-1", name: "Maya Chen", headline: "Immigration consultant" },
  pricing: { mode: "per_round", price_cents: 100, free_rounds: 2 },
  features: { knowledge: true, web_search: true, records: true, a2a: true },
  stats: { conversations: 12, a2a_calls: 3 },
  description: "Plans work visas.",
  greeting: "Hi, I plan work visas.",
  input_placeholder: "Ask about your visa",
  starter_questions: [{ label: "Brazil to Germany?", prompt: "I am from Brazil. Which German work visa fits me?" }],
  capabilities: [{ icon: "plane", name: "Visa routes", description: "Compare options" }],
  a2a: { enabled: true, price_cents: 200, card_url: "https://api.example/a2a/visa-advisor/.well-known/agent-card.json", endpoint: "https://api.example/a2a/visa-advisor" },
  status: "active",
};

const billing: BillingStatus = {
  billing_mode: "per_round",
  price_cents: 100,
  free_rounds: 2,
  is_owner: false,
  used_rounds: 2,
  free_rounds_left: 0,
  next_round_charge_cents: 100,
  balance_cents: 10_000,
  can_afford_next_round: true,
  owner_available: true,
};

const meta: Meta = {
  chat_models: ["gemini-3.8-flash"],
  default_chat_model: "gemini-3.8-flash",
  revenue_share_bp: 8000,
  signup_grant_cents: 10_000,
  demo_topup_cents: 5_000,
  demo_topup_daily_limit: 5,
  a2a_mandate_defaults: { max_amount_cents: 500, budget_cents: 2000, ttl_minutes: 30, max_budget_cents: 10_000, max_ttl_minutes: 120 },
};

const msg = (over: Partial<ChatMessage>): ChatMessage => ({
  id: "m_1",
  role: "model",
  text: "",
  status: "ok",
  turn_id: "t_restore01",
  citations: [],
  search_entry_point_html: null,
  tools: [],
  charge: null,
  created_at: "2026-10-16T08:30:00Z",
  ...over,
});

type Route = (url: URL, init: RequestInit | undefined) => Response | Promise<Response> | undefined;

function setup(route: Route) {
  const fetchMock = mockFetch();
  fetchMock.mockImplementation(async (input, init) => {
    const url = new URL(String(input));
    const custom = await route(url, init);
    if (custom) return custom;
    if (url.pathname === "/api/public/agents/visa-advisor/billing") return jsonResponse(200, billing);
    if (url.pathname === "/api/public/meta") return jsonResponse(200, meta);
    return jsonResponse(404, { code: "not_found", message: "Not found", trace_id: "t-404", detail: null });
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <AgentChat agent={agent} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return fetchMock;
}

function chatBody(init: RequestInit | undefined): { conversation_id: string | null; turn_id: string; message: string } {
  return JSON.parse(String(init?.body)) as { conversation_id: string | null; turn_id: string; message: string };
}

describe("AgentChat", () => {
  beforeEach(() => {
    sessionStorage.clear();
    ensureSignedIn.mockClear();
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("streams an answer with tool lines, citations, the search entry point and the charge footer", async () => {
    const fetchMock = setup((url, init) => {
      if (url.pathname !== "/api/public/agents/visa-advisor/chat") return undefined;
      const { turn_id } = chatBody(init);
      return sseChunks([
        frame("status", { state: "ready", conversation_id: "cnv_abc", turn_id, request_id: "r1", model: "gemini-3.8-flash" }),
        frame("tool_start", { id: "k1", tool: "search_knowledge", kind: "search", title: "Searched the knowledge base", target: "Brazil work visa" }),
        frame("tool_end", { id: "k1", tool: "search_knowledge", status: "completed" }),
        frame("tool_start", { id: "web-0", tool: "google_search", kind: "web", title: "Searched the web", target: "EU Blue Card salary" }),
        frame("tool_end", { id: "web-0", tool: "google_search", status: "completed" }),
        frame("text_delta", { delta: "The EU Blue Card fits [1]." }),
        frame("citations", {
          sources: [
            { n: 1, kind: "knowledge", title: "visa_guide.pdf", uri: null, domain: null, file_id: "kf_1", page_start: 2, page_end: 2, snippet: "Blue Card thresholds" },
            { n: 2, kind: "web", title: "make-it-in-germany.com", uri: "https://vertexaisearch.cloud.google.com/redirect/1", domain: "make-it-in-germany.com", file_id: null, page_start: null, page_end: null, snippet: null },
          ],
          search_entry_point_html: "<div>suggestions</div>",
          web_search_queries: ["EU Blue Card salary"],
        }),
        frame("done", {
          conversation_id: "cnv_abc",
          message_id: "m_9",
          turn_id,
          outcome: "ok",
          usage: null,
          billing: { charged_cents: 100, free: false, round_index: 3, free_rounds_left: 0, balance_cents: 9_900 },
        }),
      ]);
    });

    await userEvent.click(await screen.findByRole("button", { name: "Brazil to Germany?" }));

    const answer = await screen.findByTestId("model-message");
    await waitFor(() => expect(within(answer).getByTestId("charge-footer")).toHaveTextContent("Charged 1.00 · round 3 · balance 99.00"));
    expect(answer).toHaveTextContent("The EU Blue Card fits");
    expect(within(answer).getByTestId("tool-activity")).toHaveTextContent("Searched the knowledge base · Searched the web");
    const sources = within(answer).getByTestId("citations");
    expect(within(sources).getByRole("button", { name: "Source 1: visa_guide.pdf · p. 2" })).toBeInTheDocument();
    expect(within(sources).getByRole("link", { name: /make-it-in-germany\.com/ })).toHaveAttribute("target", "_blank");
    expect(within(answer).getByTitle("Google Search suggestions")).toHaveAttribute("sandbox", "allow-popups allow-popups-to-escape-sandbox");
    expect(screen.getByText("I am from Brazil. Which German work visa fits me?")).toBeInTheDocument();

    const chatCall = fetchMock.mock.calls.find(([u]) => String(u).endsWith("/chat"));
    expect(chatBody(chatCall?.[1])).toMatchObject({ conversation_id: null, message: "I am from Brazil. Which German work visa fits me?" });
    expect(sessionStorage.getItem(conversationKey("visa-advisor"))).toBe("cnv_abc");
    await waitFor(() => expect(sessionStorage.getItem(transcriptKey("visa-advisor", "cnv_abc"))).toContain("The EU Blue Card fits"));
  });

  it("opens the paywall on 402 visitor_insufficient_points and keeps the message for Resend", async () => {
    setup((url) =>
      url.pathname.endsWith("/chat")
        ? jsonResponse(402, {
            code: "visitor_insufficient_points",
            message: "You need 1.00 credits; you have 0.40.",
            trace_id: "trace-402",
            detail: { required_cents: 100, balance_cents: 40, price_cents: 100, billing_mode: "per_round", slug: "visa-advisor" },
          })
        : undefined,
    );

    await userEvent.type(await screen.findByRole("textbox", { name: "Message" }), "Is my offer enough?{Enter}");

    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("Not enough credits for this round");
    expect(within(dialog).getByRole("button", { name: /Resend/ })).toBeInTheDocument();
    expect(await within(dialog).findByRole("button", { name: "Add 50.00 demo credits" })).toBeEnabled();
    // The optimistic turn was dropped: no transcript, back to the cold start.
    expect(screen.queryByTestId("model-message")).toBeNull();
  });

  it("shows the temporarily-unavailable state when the creator cannot serve (402 insufficient_points)", async () => {
    setup((url) =>
      url.pathname.endsWith("/chat")
        ? jsonResponse(402, {
            code: "insufficient_points",
            message: "This agent is temporarily unavailable.",
            trace_id: "trace-owner",
            detail: { reason: "owner", slug: "visa-advisor" },
          })
        : undefined,
    );

    await userEvent.click(await screen.findByRole("button", { name: "Brazil to Germany?" }));

    const state = await screen.findByTestId("agent-unavailable");
    expect(state).toHaveTextContent("This agent is temporarily unavailable");
    expect(state).toHaveTextContent("Trace ID: trace-owner");
    expect(state).not.toHaveTextContent(/top up/i);
  });

  it("restores the stored conversation from the server", async () => {
    sessionStorage.setItem(conversationKey("visa-advisor"), "cnv_old");
    const conv: ConversationOut = {
      id: "cnv_old",
      agent_slug: "visa-advisor",
      title: "Visa",
      rounds_ok: 1,
      created_at: "2026-10-16T08:30:00Z",
      updated_at: "2026-10-16T08:31:00Z",
      messages: [
        msg({ id: "u_1", role: "user", text: "Earlier question" }),
        msg({ id: "m_1", text: "Earlier answer", charge: { charged_cents: 0, free: true, round_index: 1 } }),
      ],
    };
    setup((url) => (url.pathname === "/api/public/agents/visa-advisor/conversations/cnv_old" ? jsonResponse(200, conv) : undefined));

    expect(await screen.findByText("Earlier answer")).toBeInTheDocument();
    expect(screen.getByText("Earlier question")).toBeInTheDocument();
    expect(screen.getByTestId("charge-footer")).toHaveTextContent("Free round 1 of 2");
  });

  it("starts over when the stored conversation belongs to another account", async () => {
    sessionStorage.setItem(conversationKey("visa-advisor"), "cnv_other");
    setup((url) =>
      url.pathname.endsWith("/conversations/cnv_other")
        ? jsonResponse(404, { code: "session_not_owned", message: "Not yours", trace_id: "t", detail: null })
        : undefined,
    );

    expect(await screen.findByTestId("cold-start")).toBeInTheDocument();
    await waitFor(() => expect(sessionStorage.getItem(conversationKey("visa-advisor"))).toBeNull());
  });
});
