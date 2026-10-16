import { Outlet, ScrollRestoration } from "react-router";
import { Toaster } from "sonner";
import { useMeta } from "@/api/meta";
import { useTheme } from "@/lib/theme";
import { LoginDialogProvider } from "../LoginDialog";
import { LogoMark } from "./LogoMark";
import { TopNav } from "./TopNav";

function Footer() {
  return (
    <footer className="mt-auto border-t border-border bg-surface">
      <div className="page-container flex flex-col gap-2 py-6 sm:flex-row sm:items-center sm:justify-between sm:gap-5">
        <span className="flex items-center gap-2.5 text-[13px] text-muted">
          <LogoMark size={20} className="opacity-85 dark:opacity-100" />
          TopSkill · Expert skills as paid AI agents for people and agents.
        </span>
        <span className="text-[12.5px] text-subtle">Built with Gemini, ADK and A2A on Google Cloud. Credits are simulated.</span>
      </div>
    </footer>
  );
}

/** Toasts follow the theme; isolated so a theme change re-renders only this, not the whole shell. */
function ThemedToaster() {
  const { theme } = useTheme();
  return (
    // Below the 64 px top bar so a toast never covers the navigation.
    <Toaster
      position="top-center"
      offset={{ top: 64 }}
      mobileOffset={{ top: 60 }}
      theme={theme}
      richColors
      closeButton
      toastOptions={{ className: "font-sans" }}
    />
  );
}

/**
 * Root layout: top navigation, page outlet, footer, toasts and the shared LoginDialog.
 * Fetches `GET /api/public/meta` once for the whole session (query key `["meta"]`, SPEC §6.4).
 */
export function AppShell() {
  useMeta();
  return (
    <LoginDialogProvider>
      <div className="flex min-h-dvh flex-col">
        <a
          href="#main"
          className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-3 focus:z-50 focus:rounded-control focus:bg-surface focus:px-3 focus:py-2 focus:shadow-pop"
        >
          Skip to content
        </a>
        <TopNav />
        <main id="main" className="flex min-w-0 flex-1 flex-col">
          <Outlet />
        </main>
        <Footer />
      </div>
      <ThemedToaster />
      <ScrollRestoration />
    </LoginDialogProvider>
  );
}
