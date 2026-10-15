import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { queryKeys } from "@/api/queryClient";
import type { BillingStatus, ChatMessage, ConversationOut, Me, Meta, PublicAgent } from "@/api/types";
import { frame, jsonResponse, mockFetch, sseChunks, sseStream, type StreamControl } from "@/test/http";

vi.mock("@/lib/firebase", () => ({ getIdToken: async () => null }));
vi.mock("@/lib/devUser", () => ({ getDevUser: () => null }));

/**
 * Auth store for the `useAuth` mock. Like TanStack's observer, a change reaches components only
 * when subscribers are notified, so a test can reproduce "ensureSignedIn resolved, the profile
 * re-render comes a macrotask later".
 */
const authStore = vi.hoisted(() => {
  type Snapshot = { status: "signed_in" | "signed_out"; me: unknown };
  let snapshot: Snapshot = { status: "signed_out", me: null };
  const listeners = new Set<() => void>();
  return {
    get: () => snapshot,
    set(next: Snapshot) {
      snapshot = next;
      for (const l of listeners) l();
    },
    subscribe(l: () => void) {
      listeners.add(l);
      return () => listeners.delete(l);
    },
  };
});
vi.mock("@/lib/auth", async () => {
  const { useSyncExternalStore } = await import("react");
  return { useAuth: () => useSyncExternalStore(authStore.subscribe, authStore.get) };
});

const toastMock = vi.hoisted(() => ({
  toast: { info: vi.fn(), success: vi.fn(), error: vi.fn() },
  toastApiError: vi.fn(),
}));
vi.mock("@/lib/toast", () => toastMock);

const me: Me = {
  uid: "visitor-1",
  email: "ana@example.com",
  display_name: "Ana",
  photo_url: null,
  role: "visitor",
  creator_profile: null,
  created_at: "2026-10-16T08:30:00Z",
};
const ensureSignedIn = vi.fn(async () => true);
vi.mock("@/components/LoginDialog", () => ({ useLoginDialog: () => ({ ensureSignedIn }) }));

import AgentPage from "@/pages/AgentPage";
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

let lastClient: QueryClient | null = null;
const queryClientOf = () => {
  if (!lastClient) throw new Error("render first");
  return lastClient;
};

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
  lastClient = client;
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <AgentChat agent={agent} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return fetchMock;
}

const callsTo = (fetchMock: ReturnType<typeof mockFetch>, suffix: string) =>
  fetchMock.mock.calls.filter(([u]) => new URL(String(u)).pathname.endsWith(suffix));

const statusFrame = (turn_id: string, conversation_id = "cnv_s") =>
  frame("status", { state: "ready", conversation_id, turn_id, request_id: "r1", model: "gemini-3.8-flash" });

const doneFrame = (turn_id: string, over: Record<string, unknown> = {}) =>
  frame("done", {
    conversation_id: "cnv_s",
    message_id: "m_9",
    turn_id,
    outcome: "ok",
    usage: null,
    billing: { charged_cents: 100, free: false, round_index: 3, free_rounds_left: 0, balance_cents: 9_900 },
    ...over,
  });

const conversation = (id: string, messages: ChatMessage[]): ConversationOut => ({
  id,
  agent_slug: "visa-advisor",
  title: "Visa",
  rounds_ok: 1,
  created_at: "2026-10-16T08:30:00Z",
  updated_at: "2026-10-16T08:31:00Z",
  messages,
});

function chatBody(init: RequestInit | undefined): { conversation_id: string | null; turn_id: string; message: string } {
  return JSON.parse(String(init?.body)) as { conversation_id: string | null; turn_id: string; message: string };
}

describe("AgentChat", () => {
  beforeEach(() => {
    sessionStorage.clear();
    ensureSignedIn.mockReset();
    ensureSignedIn.mockImplementation(async () => true);
    for (const fn of [toastMock.toast.info, toastMock.toast.success, toastMock.toast.error, toastMock.toastApiError]) fn.mockClear();
    authStore.set({ status: "signed_in", me });
  });
  afterEach(() => {
    vi.useRealTimers();
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

  it("does not scroll an empty transcript, and pins to the bottom once a turn starts", async () => {
    const root = document.scrollingElement ?? document.documentElement;
    const heightSpy = vi.spyOn(root, "scrollHeight", "get").mockReturnValue(5_000);
    const scrollTo = vi.fn();
    vi.stubGlobal("scrollTo", scrollTo);
    try {
      setup((url, init) => {
        if (url.pathname !== "/api/public/agents/visa-advisor/chat") return undefined;
        const { turn_id } = chatBody(init);
        return sseChunks([statusFrame(turn_id), frame("text_delta", { delta: "Blue Card." }), doneFrame(turn_id)]);
      });
      const starter = await screen.findByRole("button", { name: "Brazil to Germany?" });
      expect(scrollTo).not.toHaveBeenCalled();

      await userEvent.click(starter);
      await screen.findByTestId("model-message");
      expect(scrollTo).toHaveBeenCalledWith({ top: 5_000 });
    } finally {
      heightSpy.mockRestore();
    }
  });

  it("opens the paywall on 402, keeps the draft when closed, and resends it after a top-up", async () => {
    let balance = 40;
    const fetchMock = setup((url, init) => {
      // /chat answers 402 until the top-up, then a normal stream.
      if (url.pathname === "/api/public/agents/visa-advisor/billing") {
        return jsonResponse(200, {
          ...billing,
          balance_cents: balance,
          can_afford_next_round: balance >= 100,
        } satisfies BillingStatus);
      }
      if (url.pathname === "/api/wallet/demo-topup") {
        balance += 5_000;
        return jsonResponse(200, {
          wallet: { balance_cents: balance, currency: "credits" },
          entry: { id: "le_1", amount_cents: 5_000 },
        });
      }
      if (!url.pathname.endsWith("/chat")) return undefined;
      if (balance < 100) {
        return jsonResponse(402, {
          code: "visitor_insufficient_points",
          message: "You need 1.00 credits; you have 0.40.",
          trace_id: "trace-402",
          detail: { required_cents: 100, balance_cents: 40, price_cents: 100, billing_mode: "per_round", slug: "visa-advisor" },
        });
      }
      const { turn_id } = chatBody(init);
      return sseChunks([statusFrame(turn_id), frame("text_delta", { delta: "Yes, it is enough." }), doneFrame(turn_id)]);
    });

    const box = await screen.findByRole("textbox", { name: "Message" });
    await userEvent.type(box, "Is my offer enough?{Enter}");

    let dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("Not enough credits for this round");
    expect(within(dialog).getByRole("button", { name: /Resend/ })).toBeDisabled();
    // The optimistic turn was dropped: no transcript, back to the cold start.
    expect(screen.queryByTestId("model-message")).toBeNull();

    // Closing the dialog to think about it keeps the typed question.
    await userEvent.click(within(dialog).getByText("Close", { selector: "button" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(box).toHaveValue("Is my offer enough?");

    // Send again → paywall → demo top-up → Resend sends the same message.
    await userEvent.type(box, "{Enter}");
    dialog = await screen.findByRole("dialog");
    await userEvent.click(await within(dialog).findByRole("button", { name: "Add 50.00 demo credits" }));
    await waitFor(() => expect(within(dialog).getByTestId("paywall-balance")).toHaveTextContent("50.40"));
    expect(toastMock.toast.success).toHaveBeenCalledWith("Added 50.00 demo credits");
    const resend = within(dialog).getByRole("button", { name: /Resend/ });
    await waitFor(() => expect(resend).toBeEnabled());
    await userEvent.click(resend);

    expect(await screen.findByText("Yes, it is enough.")).toBeInTheDocument();
    const chats = callsTo(fetchMock, "/chat");
    expect(chats).toHaveLength(3);
    expect(chatBody(chats[2]?.[1]).message).toBe("Is my offer enough?");
    expect(box).toHaveValue("");
  });

  it("does not show a stale top-up balance when the paywall opens again later", async () => {
    let balance = 40;
    setup((url) => {
      if (url.pathname === "/api/public/agents/visa-advisor/billing") {
        return jsonResponse(200, { ...billing, balance_cents: balance, can_afford_next_round: balance >= 100 } satisfies BillingStatus);
      }
      if (url.pathname === "/api/wallet/demo-topup") {
        balance += 5_000;
        return jsonResponse(200, { wallet: { balance_cents: balance, currency: "credits" }, entry: { id: "le_1", amount_cents: 5_000 } });
      }
      return undefined;
    });
    const hint = await screen.findByTestId("billing-hint");
    await waitFor(() => expect(hint).toHaveTextContent("Not enough credits"));
    await userEvent.click(within(hint).getByRole("button", { name: "Top up" }));
    let dialog = await screen.findByRole("dialog");
    await userEvent.click(await within(dialog).findByRole("button", { name: "Add 50.00 demo credits" }));
    await waitFor(() => expect(within(dialog).getByTestId("paywall-balance")).toHaveTextContent("50.40"));
    await userEvent.click(within(dialog).getByText("Close", { selector: "button" }));

    // Meanwhile the visitor spent it elsewhere; the next opening must show the server balance.
    balance = 30;
    await act(async () => {
      await queryClientOf().invalidateQueries({ queryKey: queryKeys.billing("visa-advisor") });
    });
    await waitFor(() => expect(screen.getByTestId("billing-hint")).toHaveTextContent("you have 0.30"));
    await userEvent.click(within(screen.getByTestId("billing-hint")).getByRole("button", { name: "Top up" }));
    dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByTestId("paywall-balance")).toHaveTextContent("0.30");
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
    expect(toastMock.toast.info).toHaveBeenCalledWith("Started a new conversation for this account");
  });

  describe("signing in from a starter question", () => {
    /** ensureSignedIn resolves at once; the auth re-render arrives a macrotask later (TanStack notify). */
    function signInLater() {
      ensureSignedIn.mockImplementation(async () => {
        setTimeout(() => authStore.set({ status: "signed_in", me }), 0);
        return true;
      });
    }

    it("shows the answer when the profile re-render lands while the stream is open", async () => {
      authStore.set({ status: "signed_out", me: null });
      signInLater();
      let stream: StreamControl | null = null;
      let turnId = "";
      const fetchMock = setup((url, init) => {
        if (!url.pathname.endsWith("/chat")) return undefined;
        turnId = chatBody(init).turn_id;
        stream = sseStream(init?.signal);
        return stream.response;
      });

      await userEvent.click(await screen.findByRole("button", { name: "Brazil to Germany?" }));
      await waitFor(() => expect(stream).not.toBeNull());
      // The signed-in render (and the billing fetch it enables) happens mid-stream.
      await waitFor(() => expect(callsTo(fetchMock, "/billing").length).toBeGreaterThan(0));
      expect(screen.getByRole("button", { name: "Stop" })).toBeInTheDocument();

      act(() => {
        stream?.push(statusFrame(turnId, "cnv_new"));
        stream?.push(frame("text_delta", { delta: "VISIBLE ANSWER" }));
        stream?.push(doneFrame(turnId, { conversation_id: "cnv_new" }));
        stream?.close();
      });

      expect(await screen.findByText("VISIBLE ANSWER")).toBeInTheDocument();
      await waitFor(() => expect(screen.getByTestId("charge-footer")).toHaveTextContent("Charged 1.00 · round 3"));
      expect(screen.getByText("I am from Brazil. Which German work visa fits me?")).toBeInTheDocument();
      expect(sessionStorage.getItem(conversationKey("visa-advisor"))).toBe("cnv_new");
    });

    it("keeps a turn that already finished before the profile re-render", async () => {
      authStore.set({ status: "signed_out", me: null });
      signInLater();
      setup((url, init) => {
        if (!url.pathname.endsWith("/chat")) return undefined;
        const { turn_id } = chatBody(init);
        return sseChunks([statusFrame(turn_id, "cnv_fast"), frame("text_delta", { delta: "FAST ANSWER" }), doneFrame(turn_id, { conversation_id: "cnv_fast" })]);
      });

      await userEvent.click(await screen.findByRole("button", { name: "Brazil to Germany?" }));
      expect(await screen.findByText("FAST ANSWER")).toBeInTheDocument();
      await waitFor(() => expect(screen.getByTestId("billing-hint")).toHaveTextContent("balance 100.00"));
      expect(screen.getByText("FAST ANSWER")).toBeInTheDocument();
      expect(screen.queryByTestId("cold-start")).toBeNull();
    });
  });

  it("Stop sends the abort with the status conversation id, then adopts a server-charged turn", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let stream: StreamControl | null = null;
    let turnId = "";
    let serverHasTurn = false;
    const fetchMock = setup((url, init) => {
      if (url.pathname.endsWith("/chat/abort")) return jsonResponse(200, { aborted: true });
      if (url.pathname.endsWith("/chat")) {
        turnId = chatBody(init).turn_id;
        stream = sseStream(init?.signal);
        return stream.response;
      }
      if (url.pathname.endsWith("/conversations/cnv_s")) {
        return jsonResponse(
          200,
          conversation("cnv_s", serverHasTurn
            ? [
                msg({ id: "u_9", role: "user", text: "Plan my move", turn_id: turnId }),
                msg({ id: "m_9", text: "Full answer from the server.", turn_id: turnId, charge: { charged_cents: 100, free: false, round_index: 3 } }),
              ]
            : []),
        );
      }
      return undefined;
    });

    await userEvent.type(await screen.findByRole("textbox", { name: "Message" }), "Plan my move{Enter}");
    await waitFor(() => expect(stream).not.toBeNull());
    act(() => {
      stream?.push(statusFrame(turnId));
      stream?.push(frame("text_delta", { delta: "Partial" }));
    });
    expect(await screen.findByText("Partial")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Stop" }));
    await waitFor(() => expect(callsTo(fetchMock, "/chat/abort")).toHaveLength(1));
    expect(JSON.parse(String(callsTo(fetchMock, "/chat/abort")[0]?.[1]?.body))).toEqual({ conversation_id: "cnv_s", turn_id: turnId });
    await waitFor(() => expect(screen.getByTestId("charge-footer")).toHaveTextContent("Not charged (stopped)"));
    expect(screen.getByTestId("chat-announcer")).toHaveTextContent("Answer stopped");

    // The model had already finished: the server settled the round as ok and charged it.
    serverHasTurn = true;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1600);
    });
    await waitFor(() => expect(screen.getByTestId("charge-footer")).toHaveTextContent("Charged 1.00 · round 3"));
    expect(screen.getByText("Full answer from the server.")).toBeInTheDocument();
    expect(sessionStorage.getItem(conversationKey("visa-advisor"))).toBe("cnv_s");
  });

  it("New conversation cancels a pending reconcile of the stopped turn", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let stream: StreamControl | null = null;
    let turnId = "";
    const fetchMock = setup((url, init) => {
      if (url.pathname.endsWith("/chat/abort")) return jsonResponse(200, { aborted: true });
      if (url.pathname.endsWith("/chat")) {
        turnId = chatBody(init).turn_id;
        stream = sseStream(init?.signal);
        return stream.response;
      }
      if (url.pathname.endsWith("/conversations/cnv_s")) {
        return jsonResponse(200, conversation("cnv_s", [
          msg({ id: "u_9", role: "user", text: "Plan my move", turn_id: turnId }),
          msg({ id: "m_9", text: "Old conversation answer.", turn_id: turnId }),
        ]));
      }
      return undefined;
    });

    await userEvent.type(await screen.findByRole("textbox", { name: "Message" }), "Plan my move{Enter}");
    await waitFor(() => expect(stream).not.toBeNull());
    act(() => stream?.push(statusFrame(turnId)));
    await userEvent.click(await screen.findByRole("button", { name: "Stop" }));
    await userEvent.click(await screen.findByRole("button", { name: "New conversation" }));
    expect(await screen.findByTestId("cold-start")).toBeInTheDocument();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(6000);
    });
    expect(callsTo(fetchMock, "/conversations/cnv_s")).toHaveLength(0);
    expect(screen.getByTestId("cold-start")).toBeInTheDocument();
    expect(sessionStorage.getItem(conversationKey("visa-advisor"))).toBeNull();
  });

  it("marks a dropped stream as Connection lost and refetches billing and the conversation", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let stream: StreamControl | null = null;
    let turnId = "";
    const fetchMock = setup((url, init) => {
      if (url.pathname.endsWith("/chat")) {
        turnId = chatBody(init).turn_id;
        stream = sseStream(init?.signal);
        return stream.response;
      }
      if (url.pathname.endsWith("/conversations/cnv_s")) return jsonResponse(200, conversation("cnv_s", []));
      return undefined;
    });

    await userEvent.type(await screen.findByRole("textbox", { name: "Message" }), "Plan my move{Enter}");
    await waitFor(() => expect(stream).not.toBeNull());
    const billingBefore = callsTo(fetchMock, "/billing").length;
    act(() => {
      stream?.push(statusFrame(turnId));
      stream?.push(frame("text_delta", { delta: "Half an" }));
    });
    await screen.findByText("Half an");
    act(() => stream?.fail());

    await waitFor(() => expect(screen.getByTestId("charge-footer")).toHaveTextContent("Connection lost"));
    await waitFor(() => expect(callsTo(fetchMock, "/billing").length).toBeGreaterThan(billingBefore));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1600);
    });
    await waitFor(() => expect(callsTo(fetchMock, "/conversations/cnv_s").length).toBeGreaterThan(0));
    // The server never stored the turn: the local "Connection lost" turn stays.
    expect(screen.getByTestId("charge-footer")).toHaveTextContent("Connection lost");
  });

  it("does not keep a minted conversation id when done says nothing was persisted", async () => {
    setup((url, init) => {
      if (!url.pathname.endsWith("/chat")) return undefined;
      const { turn_id } = chatBody(init);
      return sseChunks([
        statusFrame(turn_id, "cnv_minted"),
        frame("error", { code: "model_error", message: "The model failed.", trace_id: "tr-model" }),
        doneFrame(turn_id, { conversation_id: null, message_id: null, outcome: "failed", billing: null }),
      ]);
    });

    await userEvent.click(await screen.findByRole("button", { name: "Brazil to Germany?" }));
    await waitFor(() => expect(screen.getByTestId("charge-footer")).toHaveTextContent("Not charged"));
    expect(screen.getByText("Trace ID: tr-model")).toBeInTheDocument();
    expect(sessionStorage.getItem(conversationKey("visa-advisor"))).toBeNull();
    expect(Object.keys(sessionStorage).some((k) => k.includes("cnv_minted"))).toBe(false);
  });

  it("keeps the price (no sign-in prompt) and shows the trace id when billing fails for a signed-in visitor", async () => {
    setup((url) =>
      url.pathname === "/api/public/agents/visa-advisor/billing"
        ? jsonResponse(503, { code: "internal", message: "Billing is down", trace_id: "tr-bill", detail: null })
        : undefined,
    );
    const hint = await screen.findByTestId("billing-hint");
    await waitFor(() => expect(screen.getByText("Trace ID: tr-bill")).toBeInTheDocument());
    expect(hint).toHaveTextContent("1.00 per round · first 2 rounds free");
    expect(hint).not.toHaveTextContent(/sign in/i);
  });
});

describe("AgentPage", () => {
  beforeEach(() => {
    sessionStorage.clear();
    authStore.set({ status: "signed_in", me });
  });
  afterEach(() => vi.unstubAllGlobals());

  it("keeps the agent and its chat when a background refetch of the agent fails", async () => {
    let agentFails = false;
    const fetchMock = mockFetch();
    fetchMock.mockImplementation(async (input) => {
      const url = new URL(String(input));
      if (url.pathname === "/api/public/agents/visa-advisor") {
        return agentFails
          ? jsonResponse(503, { code: "internal", message: "Temporarily down", trace_id: "tr-503", detail: null })
          : jsonResponse(200, agent);
      }
      if (url.pathname.endsWith("/billing")) return jsonResponse(200, billing);
      if (url.pathname === "/api/public/meta") return jsonResponse(200, meta);
      return jsonResponse(404, { code: "not_found", message: "Not found", trace_id: "t", detail: null });
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={["/a/visa-advisor"]}>
          <Routes>
            <Route path="/a/:slug" element={<AgentPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
    expect(await screen.findByTestId("cold-start")).toBeInTheDocument();

    agentFails = true;
    await act(async () => {
      await client.refetchQueries({ queryKey: queryKeys.publicAgent("visa-advisor") });
    });
    expect(client.getQueryState(queryKeys.publicAgent("visa-advisor"))?.status).toBe("error");
    expect(screen.getByTestId("cold-start")).toBeInTheDocument();
    expect(screen.queryByText("Temporarily down")).toBeNull();
  });
});
