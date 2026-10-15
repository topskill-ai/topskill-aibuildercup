import { Outlet, ScrollRestoration } from "react-router";
import { Toaster } from "sonner";
import { useMeta } from "@/api/meta";
import { LoginDialogProvider } from "../LoginDialog";
import { TopNav } from "./TopNav";

function Footer() {
  return (
    <footer className="mt-auto border-t border-border bg-surface">
      <div className="page-container flex flex-col gap-1 py-6 text-[13px] text-subtle sm:flex-row sm:items-center sm:justify-between">
        <span>TopSkill · Expert skills as paid AI agents for people and agents.</span>
        <span>Built with Gemini, ADK and A2A on Google Cloud. Credits are simulated.</span>
      </div>
    </footer>
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
      <Toaster position="top-center" richColors closeButton toastOptions={{ className: "font-sans" }} />
      <ScrollRestoration />
    </LoginDialogProvider>
  );
}
