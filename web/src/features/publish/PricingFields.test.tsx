import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import { queryKeys } from "@/api/queryClient";
import type { Meta } from "@/api/types";
import { PricingFields } from "./PricingFields";
import { emptyForm, type PricingForm } from "./wizard";

const meta: Meta = {
  chat_models: ["gemini-3.8-flash"],
  default_chat_model: "gemini-3.8-flash",
  revenue_share_bp: 7000,
  signup_grant_cents: 10000,
  demo_topup_cents: 5000,
  demo_topup_daily_limit: 5,
  a2a_mandate_defaults: { max_amount_cents: 500, budget_cents: 2000, ttl_minutes: 30, max_budget_cents: 100000, max_ttl_minutes: 1440 },
};

function Harness() {
  const [form, setForm] = useState<PricingForm>(emptyForm("m").pricing);
  return <PricingFields value={form} onChange={setForm} />;
}

function renderWithMeta() {
  const qc = new QueryClient();
  qc.setQueryData(queryKeys.meta, meta);
  render(
    <QueryClientProvider client={qc}>
      <Harness />
    </QueryClientProvider>,
  );
}

describe("PricingFields", () => {
  it("shows the live pricing line from meta.revenue_share_bp and updates as the creator types", () => {
    renderWithMeta();
    const line = screen.getByTestId("pricing-line");
    expect(line).toHaveTextContent("Visitors pay 1.00 per round, first 2 rounds free; you receive 0.70 (70%).");
    expect(line).toHaveTextContent("AI agents pay 1.00 per A2A call; you receive 0.70.");

    fireEvent.change(screen.getByLabelText("Price per round (credits)"), { target: { value: "2.50" } });
    expect(line).toHaveTextContent("Visitors pay 2.50 per round, first 2 rounds free; you receive 1.75 (70%).");
    // The A2A price follows the round price until edited.
    expect(screen.getByLabelText("Price per A2A call (credits)")).toHaveValue("2.50");

    fireEvent.change(screen.getByLabelText("Price per A2A call (credits)"), { target: { value: "6" } });
    fireEvent.change(screen.getByLabelText("Price per round (credits)"), { target: { value: "1" } });
    expect(screen.getByLabelText("Price per A2A call (credits)")).toHaveValue("6");
    expect(line).toHaveTextContent("AI agents pay 6.00 per A2A call; you receive 4.20.");
  });

  it("switches to free mode", () => {
    renderWithMeta();
    fireEvent.click(screen.getByRole("radio", { name: /Free/ }));
    expect(screen.queryByLabelText("Price per round (credits)")).not.toBeInTheDocument();
    expect(screen.getByTestId("pricing-line")).toHaveTextContent("Visitors chat for free.");
  });
});
