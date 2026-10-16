import { LayoutDashboard, Plus } from "lucide-react";
import { Link, NavLink, Outlet } from "react-router";
import { cn } from "@/lib/cn";
import { buttonClasses } from "../ui/Button";

/** Sub-navigation for creator pages (`/studio/*`); the route is wrapped by `RequireCreator`. */
export function StudioLayout() {
  return (
    <div className="flex min-w-0 flex-1 flex-col">
      <div className="border-b border-border bg-surface">
        <div className="page-container flex h-12 items-center gap-2">
          <span className="mr-2 hidden text-xs font-semibold tracking-wide text-subtle uppercase sm:inline">Studio</span>
          <NavLink
            to="/studio"
            end
            className={({ isActive }) =>
              cn(
                "inline-flex h-8 items-center gap-1.5 rounded-control px-3 text-sm font-medium",
                isActive ? "bg-primary-soft text-primary" : "text-muted hover:bg-mist hover:text-text",
              )
            }
          >
            <LayoutDashboard aria-hidden className="h-4 w-4" />
            Dashboard
          </NavLink>
          <Link to="/studio/new" className={buttonClasses({ size: "sm", className: "ml-auto" })}>
            <Plus aria-hidden className="h-4 w-4" />
            Publish a skill
          </Link>
        </div>
      </div>
      <Outlet />
    </div>
  );
}
