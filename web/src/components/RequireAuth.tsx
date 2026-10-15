import type { ReactNode } from "react";
import { Navigate, Outlet, useLocation } from "react-router";
import { useAuth } from "@/lib/auth";
import { loginPath } from "@/lib/nav";
import { PageLoading } from "./layout/PageHeader";

/**
 * Route guard for signed-in pages (`/assistant`, `/wallet`): redirects to `/login?next=…`
 * when signed out (SPEC §11.2). Renders `children`, or the nested route via `<Outlet/>`.
 */
export function RequireAuth({ children }: { children?: ReactNode }) {
  const { status } = useAuth();
  const location = useLocation();
  if (status === "loading") return <PageLoading />;
  if (status === "signed_out") {
    return <Navigate to={loginPath(location.pathname, location.search, location.hash)} replace />;
  }
  return <>{children ?? <Outlet />}</>;
}
