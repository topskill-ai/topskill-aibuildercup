import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Page, PublicAgentCard } from "@/api/types";
import { jsonResponse, mockFetch } from "@/test/http";

vi.mock("@/lib/firebase", () => ({ getIdToken: async () => null }));
vi.mock("@/lib/devUser", () => ({ getDevUser: () => null }));

import { Marketplace } from "./Marketplace";
import { priceChipLabel, priceChipParts, priceSentence } from "./pricing";

const card = (over: Partial<PublicAgentCard>): PublicAgentCard => ({
  slug: "visa-advisor",
  title: "Visa Advisor",
  tagline: "Work visa plans with cited sources",
  avatar_url: null,
  accent_color: "blue",
  creator: { uid: "c1", name: "Maya Chen", headline: "Immigration consultant" },
  pricing: { mode: "per_round", price_cents: 100, free_rounds: 2 },
  features: { knowledge: true, web_search: true, records: true, a2a: true },
  stats: { conversations: 1200, a2a_calls: 3 },
  ...over,
});

function renderMarketplace(initial = "/") {
  const router = createMemoryRouter([{ path: "/", element: <Marketplace /> }, { path: "/a/:slug", element: <div>agent page</div> }], {
    initialEntries: [initial],
  });
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}

describe("Marketplace", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("renders agent cards with price chip, creator and feature badges", async () => {
    const fetchMock = mockFetch();
    const page: Page<PublicAgentCard> = {
      items: [
        card({}),
        card({ slug: "resume-reviewer", title: "Resume Reviewer", pricing: { mode: "free", price_cents: 0, free_rounds: 0 }, features: { knowledge: false, web_search: false, records: false, a2a: true } }),
      ],
      next_cursor: null,
    };
    fetchMock.mockResolvedValue(jsonResponse(200, page));
    const router = renderMarketplace();

    const list = await screen.findByRole("list", { name: "Agents" });
    const cards = within(list).getAllByRole("link");
    expect(cards).toHaveLength(2);
    expect(cards[0]).toHaveTextContent("Visa Advisor");
    expect(cards[0]).toHaveTextContent("by Maya Chen");
    expect(within(cards[0]).getByTestId("price-chip")).toHaveTextContent("1.00 / round · 2 free");
    expect(within(cards[0]).getByRole("list", { name: "Features" })).toHaveTextContent("KnowledgeWebRecordsA2A");
    expect(within(cards[1]).getByTestId("price-chip")).toHaveTextContent("Free");

    const url = new URL(String(fetchMock.mock.calls[0][0]));
    expect(url.pathname).toBe("/api/public/agents");
    expect(url.searchParams.get("limit")).toBe("24");
    expect(url.searchParams.has("q")).toBe(false);

    await userEvent.click(cards[0]);
    expect(router.state.location.pathname).toBe("/a/visa-advisor");
  });

  it("searches with a debounced q, mirrors it in the URL and shows an empty state", async () => {
    const fetchMock = mockFetch();
    fetchMock.mockImplementation(async (input) => {
      const q = new URL(String(input)).searchParams.get("q");
      return jsonResponse(200, { items: q ? [] : [card({})], next_cursor: null });
    });
    const router = renderMarketplace();
    await screen.findByRole("list", { name: "Agents" });

    await userEvent.type(screen.getByRole("searchbox", { name: "Search agents" }), "tax");

    expect(await screen.findByText("No agents match “tax”")).toBeInTheDocument();
    const searched = fetchMock.mock.calls.map(([u]) => new URL(String(u)).searchParams.get("q")).filter(Boolean);
    expect(searched).toEqual(["tax"]);
    await waitFor(() => expect(router.state.location.search).toBe("?q=tax"));

    // Both the field's X and the empty state's button clear the search.
    const clear = screen.getAllByRole("button", { name: "Clear search" });
    expect(clear).toHaveLength(2);
    await userEvent.click(clear[1]);
    expect(await screen.findByRole("list", { name: "Agents" })).toBeInTheDocument();
  });

  it("shows the error with its trace id", async () => {
    const fetchMock = mockFetch();
    fetchMock.mockResolvedValue(
      jsonResponse(503, { code: "service_unavailable", message: "Try again shortly.", trace_id: "trace-m", detail: null }),
    );
    renderMarketplace();
    expect(await screen.findByRole("alert")).toHaveTextContent("Try again shortly.");
    expect(screen.getByTestId("trace-id")).toHaveTextContent("trace-m");
  });
});

describe("pricing copy", () => {
  it("formats chips and sentences from integer cents", () => {
    expect(priceChipLabel({ mode: "per_round", price_cents: 100, free_rounds: 2 })).toBe("1.00 / round · 2 free");
    expect(priceChipLabel({ mode: "per_round", price_cents: 250, free_rounds: 0 })).toBe("2.50 / round");
    expect(priceChipLabel({ mode: "free", price_cents: 0, free_rounds: 0 })).toBe("Free");
    expect(priceChipParts({ mode: "per_round", price_cents: 100, free_rounds: 2 })).toEqual({
      amount: "1.00",
      unit: " / round · 2 free",
    });
    expect(priceChipParts({ mode: "per_round", price_cents: 0, free_rounds: 3 })).toEqual({ amount: "Free", unit: "" });
    expect(priceSentence({ mode: "per_round", price_cents: 100, free_rounds: 1 })).toBe("1.00 credits per round, first 1 round free.");
    expect(priceSentence({ mode: "per_round", price_cents: 100, free_rounds: 2 })).toBe("1.00 credits per round, first 2 rounds free.");
  });
});
