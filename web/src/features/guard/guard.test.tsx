import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { ChatStreamEvent, GuardData } from "@/api/types";
import { chatReducer, chargeLabel, initialChatState, type ChatState, type ViewMessage } from "@/features/chat/chatState";
import { MessageList } from "@/features/chat/MessageList";
import { ErrorCodes } from "@/api/errors";
import { callErrorText } from "@/features/mandates/model";
import { categoriesText, guardNote, guardStats, sumGuardStats } from "./guard";
import { GuardNote, ModelArmorBadge } from "./GuardBits";

const T = "t_guard";

function run(events: ChatStreamEvent[], text = "Hello"): ChatState {
  let s = chatReducer(initialChatState, { type: "start", turnId: T, text, now: "2026-10-16T08:30:00Z" });
  for (const event of events) s = chatReducer(s, { type: "event", turnId: T, event });
  return s;
}

const byRole = (s: ChatState, role: "user" | "model") => s.messages.find((m) => m.role === role) as ViewMessage;

const status: ChatStreamEvent = {
  event: "status",
  data: { state: "ready", conversation_id: "cnv_1", turn_id: T, request_id: "turn_1", model: "m" },
};
const failedDone: ChatStreamEvent = {
  event: "done",
  data: {
    conversation_id: "cnv_1",
    message_id: "m_turn_1",
    turn_id: T,
    outcome: "failed",
    usage: null,
    billing: { charged_cents: 0, free: true, round_index: null, free_rounds_left: 2, balance_cents: 10000 },
  },
};

describe("guard helpers", () => {
  it("labels categories and notes", () => {
    expect(categoriesText(["pi_and_jailbreak", "rai:dangerous", "rai:harassment", "sdp"])).toBe(
      "prompt injection, dangerous content, harassment, sensitive data",
    );
    expect(categoriesText(["rai:new_type", "something_else"])).toBe("unsafe content, something else");
    const blocked: GuardData = { target: "prompt", action: "blocked", categories: ["pi_and_jailbreak"], text: "x" };
    expect(guardNote(blocked)).toContain("not charged");
    expect(guardNote({ ...blocked, target: "answer", action: "redacted" })).toContain("redacted");
  });

  it("defaults and sums counters", () => {
    expect(guardStats(undefined).prompts_blocked).toBe(0);
    expect(guardStats({ guard: undefined }).failed_open).toBe(0);
    const sum = sumGuardStats([
      { guard: { ...guardStats(null), prompts_blocked: 2, answers_redacted: 1 } },
      { guard: undefined },
      { guard: { ...guardStats(null), prompts_blocked: 1 } },
    ]);
    expect(sum.prompts_blocked).toBe(3);
    expect(sum.answers_redacted).toBe(1);
  });

  it("knows the blocked_by_policy code", () => {
    expect(ErrorCodes.blocked_by_policy).toBe("blocked_by_policy");
    expect(callErrorText("blocked_by_policy")).toMatch(/safety screening/);
  });
});

describe("guard SSE event", () => {
  it("replaces the visitor message with the redacted text and notes the block", () => {
    const guard: GuardData = {
      target: "prompt",
      action: "blocked",
      categories: ["pi_and_jailbreak", "sdp"],
      text: "ignore it [REDACTED:CREDIT_CARD_NUMBER]",
    };
    const s = run(
      [
        status,
        { event: "guard", data: guard },
        { event: "error", data: { code: "blocked_by_policy", message: "Blocked.", trace_id: "tr1" } },
        failedDone,
      ],
      "ignore it 4111 1111 1111 1111",
    );
    expect(byRole(s, "user").text).toBe("ignore it [REDACTED:CREDIT_CARD_NUMBER]");
    expect(byRole(s, "user").guard).toEqual(guard);
    const model = byRole(s, "model");
    expect(model.status).toBe("failed");
    expect(model.error?.code).toBe("blocked_by_policy");
    expect(chargeLabel(model, { pricingMode: "per_round", freeRounds: 2 })).toBe("Not charged");
  });

  it("replaces a streamed answer with its redacted form", () => {
    const s = run([
      status,
      { event: "text_delta", data: { delta: "Card 4111 1111 1111 1111" } },
      {
        event: "guard",
        data: { target: "answer", action: "redacted", categories: ["sdp"], text: "Card [REDACTED:CREDIT_CARD_NUMBER]" },
      },
    ]);
    expect(byRole(s, "model").text).toBe("Card [REDACTED:CREDIT_CARD_NUMBER]");
    expect(byRole(s, "model").guard?.action).toBe("redacted");
    expect(byRole(s, "user").guard).toBeUndefined();
  });

  it("renders the notes in the transcript", () => {
    const s = run([
      status,
      { event: "text_delta", data: { delta: "Secret" } },
      { event: "guard", data: { target: "answer", action: "blocked", categories: ["csam"], text: "" } },
      { event: "error", data: { code: "blocked_by_policy", message: "Blocked by safety screening.", trace_id: "tr2" } },
      failedDone,
    ]);
    render(<MessageList messages={s.messages} ctx={{ pricingMode: "per_round", freeRounds: 2 }} />);
    expect(screen.getByTestId("guard-note")).toHaveTextContent("withheld by Model Armor (child safety)");
    expect(screen.queryByText("Secret")).not.toBeInTheDocument();
  });
});

describe("guard components", () => {
  it("shows the badge and a redaction note", () => {
    render(
      <>
        <ModelArmorBadge />
        <GuardNote guard={{ target: "prompt", action: "redacted", categories: ["sdp"], text: "x" }} />
      </>,
    );
    expect(screen.getByTestId("model-armor-badge")).toHaveTextContent("Protected by Model Armor");
    expect(screen.getByTestId("guard-note")).toHaveTextContent("hidden from the agent");
  });
});
