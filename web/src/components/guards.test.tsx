import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Me } from "@/api/types";

const authState = vi.hoisted(() => ({ value: {} as Record<string, unknown> }));
vi.mock("@/lib/auth", () => ({ useAuth: () => authState.value }));

import { ApiError } from "@/api/errors";
import { RequireAuth } from "./RequireAuth";
import { RequireCreator } from "./RequireCreator";
import { ErrorLine } from "./ui/ErrorLine";

const me = (role: Me["role"]): Me => ({
  uid: "u1",
  email: "ana@example.com",
  display_name: "Ana",
  photo_url: null,
  role,
  creator_profile: null,
  created_at: "2026-10-16T08:30:00Z",
});

function renderAt(path: string, element: ReactNode) {
  const router = createMemoryRouter(
    [
      { path: "/login", element: <div>login page</div> },
      { path: "/wallet", element: <RequireAuth>{element}</RequireAuth> },
      { path: "/studio", element: <RequireCreator>{element}</RequireCreator> },
    ],
    { initialEntries: [path] },
  );
  render(
    <QueryClientProvider client={new QueryClient()}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}

describe("route guards", () => {
  beforeEach(() => {
    authState.value = { status: "signed_out", me: null, meError: null, setMe: vi.fn(), refetchMe: vi.fn() };
  });

  it("RequireAuth redirects signed-out users to /login?next=", () => {
    const router = renderAt("/wallet", <div>wallet</div>);
    expect(screen.getByText("login page")).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/login");
    expect(router.state.location.search).toBe("?next=%2Fwallet");
  });

  it("RequireAuth renders the page when signed in", () => {
    authState.value = { ...authState.value, status: "signed_in", me: me("visitor") };
    renderAt("/wallet", <div>wallet</div>);
    expect(screen.getByText("wallet")).toBeInTheDocument();
  });

  it("RequireCreator shows the become-creator card to visitors", () => {
    authState.value = { ...authState.value, status: "signed_in", me: me("visitor") };
    renderAt("/studio", <div>studio</div>);
    expect(screen.getByRole("heading", { name: "Become a creator" })).toBeInTheDocument();
    expect(screen.queryByText("studio")).toBeNull();
  });

  it("RequireCreator renders the page for creators", () => {
    authState.value = { ...authState.value, status: "signed_in", me: me("creator") };
    renderAt("/studio", <div>studio</div>);
    expect(screen.getByText("studio")).toBeInTheDocument();
  });
});

describe("ErrorLine", () => {
  it("shows the message and the trace id", () => {
    render(<ErrorLine error={new ApiError(503, "service_unavailable", "Try again shortly.", "trace-42")} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Try again shortly.");
    expect(screen.getByTestId("trace-id")).toHaveTextContent("Trace ID: trace-42");
  });
});
