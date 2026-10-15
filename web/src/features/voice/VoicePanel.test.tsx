import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { VoiceController } from "./useVoice";
import { VoiceButton, VoicePanel } from "./VoicePanel";
import { initialVoiceState, type VoiceState } from "./voiceState";

function controller(state: Partial<VoiceState>): VoiceController {
  return {
    state: { ...initialVoiceState, ...state },
    start: vi.fn(async () => undefined),
    end: vi.fn(),
    setMuted: vi.fn(),
    reset: vi.fn(),
    onLevel: vi.fn(),
  };
}

const live: Partial<VoiceState> = { phase: "live", startedAt: Date.now(), maxSessionS: 600 };

describe("VoicePanel", () => {
  it("shows live captions, tool lines and the charge of each round", () => {
    const voice = controller({
      ...live,
      turns: [
        { turnId: "v0", user: "Old", model: "Old answer", tools: [], outcome: "ok", billing: null, interrupted: false },
        {
          turnId: "v1",
          user: "What is the fee?",
          model: "It is 290 USD.",
          tools: [{ id: "c1", tool: "search_knowledge", kind: "search", title: "Searched the knowledge base", target: "fee", status: "completed" }],
          outcome: "ok",
          billing: { charged_cents: 100, free: false, round_index: 3, free_rounds_left: 0, balance_cents: 900 },
          interrupted: false,
        },
        { turnId: "v2", user: "And how long?", model: "", tools: [], outcome: null, billing: null, interrupted: false },
      ],
    });
    render(<VoicePanel voice={voice} agentTitle="Visa Advisor" />);
    expect(screen.getByTestId("voice-status")).toHaveTextContent("Thinking…");
    expect(screen.getByText("0:00 / 10:00")).toBeInTheDocument();
    const captions = screen.getAllByTestId("voice-caption");
    expect(captions).toHaveLength(2); // the last two rounds only
    expect(within(captions[0]).getByText("It is 290 USD.")).toBeInTheDocument();
    expect(within(captions[0]).getByText(/Searched the knowledge base/)).toBeInTheDocument();
    expect(screen.getByTestId("voice-charge")).toHaveTextContent("Charged 1.00 · balance 9.00");
    expect(within(captions[1]).getByText("And how long?")).toBeInTheDocument();
  });

  it("mutes and hangs up", async () => {
    const voice = controller(live);
    render(<VoicePanel voice={voice} agentTitle="Visa Advisor" />);
    expect(screen.getByTestId("voice-status")).toHaveTextContent("Listening");
    expect(screen.getByText(/ask your question/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Mute microphone" }));
    expect(voice.setMuted).toHaveBeenCalledWith(true);
    await userEvent.click(screen.getByRole("button", { name: /End/ }));
    expect(voice.end).toHaveBeenCalled();
  });

  it("shows an error with its trace id and a retry", async () => {
    const voice = controller({
      phase: "error",
      error: { code: "service_unavailable", message: "Voice is temporarily unavailable.", traceId: "abc123", detail: null },
    });
    render(<VoicePanel voice={voice} agentTitle="Visa Advisor" />);
    expect(screen.getByTestId("voice-status")).toHaveTextContent("Call failed");
    expect(screen.getByRole("alert")).toHaveTextContent("Voice is temporarily unavailable.");
    expect(screen.getByTestId("trace-id")).toHaveTextContent("abc123");
    await userEvent.click(screen.getByRole("button", { name: /Try again/ }));
    expect(voice.start).toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(voice.reset).toHaveBeenCalled();
  });

  it("says why a call ended", () => {
    render(<VoicePanel voice={controller({ phase: "ended", closedReason: "max_duration", rounds: 2 })} agentTitle="A" />);
    expect(screen.getByTestId("voice-status")).toHaveTextContent("Call ended");
    expect(screen.getByText("2 answered questions")).toBeInTheDocument();
    expect(screen.getByText(/time limit/)).toBeInTheDocument();
  });

  it("speaking state while audio plays", () => {
    render(<VoicePanel voice={controller({ ...live, playing: true })} agentTitle="A" />);
    expect(screen.getByTestId("voice-status")).toHaveTextContent("Speaking");
  });
});

describe("VoiceButton", () => {
  it("starts a call", async () => {
    const onClick = vi.fn();
    render(<VoiceButton onClick={onClick} />);
    await userEvent.click(screen.getByRole("button", { name: "Start a voice call" }));
    expect(onClick).toHaveBeenCalled();
  });
});
