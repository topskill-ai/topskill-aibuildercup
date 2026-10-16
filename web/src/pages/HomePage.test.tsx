import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { jsonResponse, mockFetch } from "@/test/http";

vi.mock("@/lib/firebase", () => ({ getIdToken: async () => null }));
vi.mock("@/lib/devUser", () => ({ getDevUser: () => null }));

import { ThemeToggle } from "@/components/layout/ThemeToggle";
import { THEME_STORAGE_KEY } from "@/lib/theme";
import HomePage from "./HomePage";

function renderHome() {
  const router = createMemoryRouter(
    [
      { path: "/", element: <HomePage /> },
      { path: "/studio/new", element: <div>publish wizard</div> },
      { path: "/assistant", element: <div>assistant page</div> },
    ],
    { initialEntries: ["/"] },
  );
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}

describe("HomePage", () => {
  beforeEach(() => {
    mockFetch().mockResolvedValue(jsonResponse(200, { items: [], next_cursor: null }));
  });
  afterEach(() => vi.unstubAllGlobals());

  it("renders the two-column hero with headline, highlights and illustration", () => {
    renderHome();
    const hero = screen.getByRole("region", { name: /Turn any expert's skill into a paid AI agent in 60 seconds/ });
    expect(within(hero).getByRole("heading", { level: 1 })).toHaveTextContent(
      "Turn any expert's skill into a paid AI agent in 60 seconds",
    );
    expect(within(hero).getByText("Expert skills as paid AI agents")).toBeInTheDocument();
    expect(within(hero).getByRole("list", { name: "Highlights" })).toHaveTextContent(
      "No code requiredA2A protocol readyPer-call revenue share",
    );
    const art = within(hero).getByRole("img", { name: /expert publishing her skill/ });
    expect(art).toHaveAttribute("src", expect.stringContaining("hero-illustration"));
    // Decorative floating cards: present but hidden from assistive tech.
    expect(within(hero).getByText("Agent call answered").closest("[aria-hidden]")).not.toBeNull();
    expect(within(hero).getByText("+12.40 credits earned").closest("[aria-hidden]")).not.toBeNull();
  });

  it("links the hero calls to action", async () => {
    const router = renderHome();
    expect(screen.getByRole("link", { name: "Try the Enterprise Assistant" })).toHaveAttribute("href", "/assistant");
    await userEvent.click(screen.getByRole("link", { name: "Publish a skill" }));
    expect(router.state.location.pathname).toBe("/studio/new");
  });

  it("lists the three numbered steps and embeds the marketplace", async () => {
    renderHome();
    const how = screen.getByRole("region", { name: "How it works" });
    const steps = within(how).getAllByRole("listitem");
    expect(steps).toHaveLength(3);
    expect(steps.map((s) => within(s).getByRole("heading", { level: 3 }).textContent)).toEqual([
      "Drop a skill",
      "People and agents use it",
      "You earn per use",
    ]);
    expect(steps[0]).toHaveTextContent("01");
    expect(steps[0]).toHaveTextContent("Step 1");
    expect(steps[2]).toHaveTextContent("03");

    expect(screen.getByRole("heading", { level: 2, name: "Marketplace" })).toBeInTheDocument();
    expect(screen.getByRole("searchbox", { name: "Search agents" })).toBeInTheDocument();
    expect(await screen.findByText("No agents published yet")).toBeInTheDocument();
  });
});

describe("ThemeToggle", () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.setAttribute("data-theme", "light");
  });

  it("switches theme, updates its accessible name and remembers the choice", async () => {
    render(<ThemeToggle />);
    await userEvent.click(screen.getByRole("button", { name: "Switch to dark theme" }));
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe("dark");

    await userEvent.click(screen.getByRole("button", { name: "Switch to light theme" }));
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe("light");
  });
});
