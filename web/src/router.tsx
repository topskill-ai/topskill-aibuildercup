/**
 * Route table (SPEC §11.2). Pages are lazy-loaded; each page file default-exports its component.
 * Owners replace the placeholder pages; the paths and guards here stay fixed.
 */
import type { ComponentType } from "react";
import { createBrowserRouter, type RouteObject } from "react-router";
import { AppShell } from "./components/layout/AppShell";
import { RouteError } from "./components/layout/RouteError";
import { StudioLayout } from "./components/layout/StudioLayout";
import { RequireAuth } from "./components/RequireAuth";
import { RequireCreator } from "./components/RequireCreator";

type PageModule = { default: ComponentType };

function page(load: () => Promise<PageModule>): Pick<RouteObject, "lazy"> {
  return { lazy: async () => ({ Component: (await load()).default }) };
}

export const routes: RouteObject[] = [
  {
    path: "/",
    element: <AppShell />,
    errorElement: <RouteError />,
    children: [
      {
        errorElement: <RouteError />,
        children: [
          { index: true, ...page(() => import("./pages/HomePage")) },
          { path: "login", ...page(() => import("./pages/LoginPage")) },
          { path: "a/:slug", ...page(() => import("./pages/AgentPage")) },
          {
            element: <RequireAuth />,
            children: [
              { path: "assistant", ...page(() => import("./pages/AssistantPage")) },
              { path: "wallet", ...page(() => import("./pages/WalletPage")) },
            ],
          },
          {
            path: "studio",
            element: (
              <RequireCreator>
                <StudioLayout />
              </RequireCreator>
            ),
            children: [
              { index: true, ...page(() => import("./pages/StudioDashboardPage")) },
              { path: "new", ...page(() => import("./pages/PublishWizardPage")) },
              { path: "agents/:id", ...page(() => import("./pages/AgentDetailPage")) },
              { path: "agents/:id/records/:recordId", ...page(() => import("./pages/RecordDetailPage")) },
            ],
          },
          { path: "*", ...page(() => import("./pages/NotFoundPage")) },
        ],
      },
    ],
  },
];

export function createAppRouter() {
  return createBrowserRouter(routes);
}
