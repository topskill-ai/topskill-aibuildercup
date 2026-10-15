import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { BillingStatus, ChatMessage, ConversationOut, PublicAgent } from "@/api/types";
import { jsonResponse, mockFetch } from "@/test/http";
import type { UseVoiceOptions, VoiceController } from "./useVoice";
import { initialVoiceState, type VoiceState } from "./voiceState";

vi.mock("@/lib/firebase", () => ({ getIdToken: async () => null }));
vi.mock("@/lib/devUser", () => ({ getDevUser: () => null }));
vi.mock("@/lib/auth", () => ({
  useAuth: () => ({ status: "signed_in", me: { uid: "visitor-1", role: "visitor" } }),
}));
vi.mock("@/components/LoginDialog", () => ({ useLoginDialog: () => ({ ensureSignedIn: async () => true }) }));

/** The real hook is covered by useVoice.test.tsx; here it is a controllable stand-in. */
const voiceMock = vi.hoisted(() => ({
  state: null as unknown as VoiceState,
  opts: null as unknown as UseVoiceOptions,
  start: vi.fn(async () => undefined),
}));
vi.mock("./useVoice", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./useVoice")>();
  return {
    ...actual,
    useVoice: (opts: UseVoiceOptions): VoiceController => {
      voiceMock.opts = opts;
      return {
        state: voiceMock.state,
        start: voiceMock.start,
        end: vi.fn(),
        setMuted: vi.fn(),
        reset: vi.fn(),
        onLevel: vi.fn(),
      };
    },
  };
});

import { AgentChat } from "@/features/chat/AgentChat";

const agent: PublicAgent = {
  slug: "visa-advisor",
  title: "Visa Advisor",
  tagline: "Work visa plans",
  avatar_url: null,
  accent_color: "blue",
  creator: { uid: "creator-1", name: "Maya Chen", headline: "" },
  pricing: { mode: "per_round", price_cents: 100, free_rounds: 2 },
  features: { knowledge: true, web_search: true, records: false, a2a: false },
  stats: { conversations: 1, a2a_calls: 0 },
  description: "",
  greeting: "Hi",
  input_placeholder: "Ask about your visa",
  starter_questions: [],
  capabilities: [],
  a2a: { enabled: false, price_cents: 0, card_url: "", endpoint: "" },
  status: "active",
};

const billing: BillingStatus = {
  billing_mode: "per_round",
  price_cents: 100,
  free_rounds: 2,
  is_owner: false,
  used_rounds: 0,
  free_rounds_left: 2,
  next_round_charge_cents: 0,
  balance_cents: 10_000,
  can_afford_next_round: true,
  owner_available: true,
};

const voiceMessages: ChatMessage[] = [
  {
    id: "u_voice_1",
    role: "user",
    text: "What is the fee?",
    status: "ok",
    turn_id: "voice_abc12345",
    citations: [],
    search_entry_point_html: null,
    tools: [],
    charge: null,
    created_at: "2026-10-16T08:30:00Z",
  },
  {
    id: "m_voice_1",
    role: "model",
    text: "It is 290 USD.",
    status: "ok",
    turn_id: "voice_abc12345",
    citations: [],
    search_entry_point_html: null,
    tools: [],
    charge: { charged_cents: 0, free: true, round_index: 1 },
    created_at: "2026-10-16T08:30:05Z",
  },
];

function setup(voiceEnabled: boolean | null) {
  const fetchMock = mockFetch();
  fetchMock.mockImplementation(async (input) => {
    const url = new URL(String(input));
    if (url.pathname === "/api/public/voice/config") {
      if (voiceEnabled === null) return jsonResponse(404, { code: "not_found", message: "", trace_id: "", detail: null });
      return jsonResponse(200, { enabled: voiceEnabled, max_session_s: 600, idle_timeout_s: 90, input_sample_rate: 16000, output_sample_rate: 24000 });
    }
    if (url.pathname === "/api/public/agents/visa-advisor/billing") return jsonResponse(200, billing);
    if (url.pathname === "/api/public/agents/visa-advisor/conversations/cnv_v") {
      const conv: ConversationOut = { id: "cnv_v", agent_slug: "visa-advisor", title: "Fee", rounds_ok: 1, created_at: "", updated_at: "", messages: voiceMessages };
      return jsonResponse(200, conv);
    }
    return jsonResponse(404, { code: "not_found", message: "Not found", trace_id: "t", detail: null });
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <AgentChat agent={agent} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { fetchMock, view };
}

beforeEach(() => {
  sessionStorage.clear();
  voiceMock.state = initialVoiceState;
  voiceMock.start.mockClear();
});
afterEach(() => vi.unstubAllGlobals());

describe("AgentChat voice mode", () => {
  it("offers the mic only when the backend enables voice", async () => {
    setup(true);
    await userEvent.click(await screen.findByRole("button", { name: "Start a voice call" }));
    expect(voiceMock.start).toHaveBeenCalled();
    expect(voiceMock.opts.conversationId).toBeNull();
  });

  it.each([false, null])("hides the mic when voice is %s", async (enabled) => {
    const { fetchMock } = setup(enabled);
    await waitFor(() =>
      expect(fetchMock.mock.calls.some(([u]) => String(u).endsWith("/api/public/voice/config"))).toBe(true),
    );
    expect(screen.getByRole("textbox", { name: "Message" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Start a voice call" })).not.toBeInTheDocument();
  });

  it("replaces the composer with the call panel and shows the spoken rounds in the transcript", async () => {
    voiceMock.state = { ...initialVoiceState, phase: "live", startedAt: Date.now(), maxSessionS: 600 };
    setup(true);
    expect(await screen.findByTestId("voice-panel")).toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: "Message" })).not.toBeInTheDocument();

    act(() => voiceMock.opts.onRound?.("cnv_v"));
    expect(await screen.findByText("It is 290 USD.")).toBeInTheDocument();
    expect(screen.getByTestId("voice-marker")).toBeInTheDocument();
    expect(sessionStorage.getItem("topskill.chat.visa-advisor")).toBe("cnv_v");
  });
});
