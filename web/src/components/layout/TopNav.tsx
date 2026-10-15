import { useQuery } from "@tanstack/react-query";
import { Bot, ChevronDown, LayoutDashboard, LogOut, Menu, Store, Wallet as WalletIcon, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link, NavLink, useLocation } from "react-router";
import { api } from "@/api/client";
import { queryKeys } from "@/api/queryClient";
import type { Wallet } from "@/api/types";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/cn";
import { useLoginDialog } from "../LoginDialog";
import { Avatar } from "../ui/Avatar";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { Money } from "../ui/Money";

const NAV = [
  { to: "/", label: "Marketplace", icon: Store, end: true },
  { to: "/assistant", label: "Assistant", icon: Bot, end: false },
  { to: "/studio", label: "Studio", icon: LayoutDashboard, end: false },
  { to: "/wallet", label: "Wallet", icon: WalletIcon, end: false },
] as const;

export function Logo({ className }: { className?: string }) {
  return (
    <Link to="/" className={cn("flex shrink-0 items-center gap-2 font-semibold tracking-display text-text", className)}>
      <span className="flex h-7 w-7 items-center justify-center rounded-[8px] bg-primary text-[13px] font-bold text-white">
        T
      </span>
      <span className="text-[17px]">TopSkill</span>
    </Link>
  );
}

function useWalletBalance(enabled: boolean) {
  return useQuery({
    queryKey: queryKeys.wallet,
    queryFn: () => api.get<Wallet>("/api/wallet"),
    enabled,
    staleTime: 15_000,
  });
}

function UserMenu() {
  const { me, signOut, devAuthEnabled } = useAuth();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const name = me?.display_name || me?.email || "Account";
  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-1.5 rounded-chip p-0.5 pr-1.5 hover:bg-slate-100"
      >
        <Avatar src={me?.photo_url} name={name} size={30} accent="violet" />
        <ChevronDown aria-hidden className="h-4 w-4 text-subtle" />
        <span className="sr-only">Account menu</span>
      </button>
      {open ? (
        <div
          role="menu"
          className="absolute right-0 z-40 mt-2 w-64 rounded-card border border-border bg-surface p-1.5 shadow-pop animate-fade-in"
        >
          <div className="px-3 py-2">
            <div className="truncate text-sm font-semibold">{name}</div>
            {me?.email ? <div className="truncate text-xs text-muted">{me.email}</div> : null}
            <div className="mt-1.5 flex flex-wrap gap-1">
              <Badge variant={me?.role === "creator" ? "primary" : "neutral"}>
                {me?.role === "creator" ? "Creator" : "Visitor"}
              </Badge>
              {devAuthEnabled ? <Badge variant="warning">Dev auth</Badge> : null}
            </div>
          </div>
          <div className="my-1 h-px bg-border" />
          <Link
            role="menuitem"
            to="/wallet"
            onClick={() => setOpen(false)}
            className="flex items-center gap-2 rounded-control px-3 py-2 text-sm text-text hover:bg-slate-50"
          >
            <WalletIcon aria-hidden className="h-4 w-4 text-subtle" /> Wallet
          </Link>
          <button
            role="menuitem"
            type="button"
            onClick={() => {
              setOpen(false);
              void signOut();
            }}
            className="flex w-full items-center gap-2 rounded-control px-3 py-2 text-left text-sm text-text hover:bg-slate-50"
          >
            <LogOut aria-hidden className="h-4 w-4 text-subtle" /> Sign out
          </button>
        </div>
      ) : null}
    </div>
  );
}

export function TopNav() {
  const { status } = useAuth();
  const { openLogin } = useLoginDialog();
  const location = useLocation();
  const [mobileOpen, setMobileOpen] = useState(false);
  const signedIn = status === "signed_in";
  const wallet = useWalletBalance(signedIn);
  const [lastPath, setLastPath] = useState(location.pathname);

  // Close the mobile menu on navigation.
  if (lastPath !== location.pathname) {
    setLastPath(location.pathname);
    setMobileOpen(false);
  }

  const linkClass = ({ isActive }: { isActive: boolean }) =>
    cn(
      "inline-flex h-9 items-center gap-1.5 rounded-control px-3 text-sm font-medium transition-colors duration-150",
      isActive ? "bg-primary-soft text-primary" : "text-muted hover:bg-slate-100 hover:text-text",
    );

  return (
    <header className="sticky top-0 z-30 border-b border-border bg-surface/90 backdrop-blur supports-[backdrop-filter]:bg-surface/80">
      <div className="page-container flex h-14 items-center gap-3">
        <Logo />
        <nav aria-label="Main" className="ml-4 hidden items-center gap-1 md:flex">
          {NAV.map(({ to, label, end }) => (
            <NavLink key={to} to={to} end={end} className={linkClass}>
              {label}
            </NavLink>
          ))}
        </nav>
        <div className="ml-auto flex items-center gap-2">
          {signedIn && wallet.data ? (
            <Link
              to="/wallet"
              className="hidden items-center gap-1.5 rounded-chip border border-border px-3 py-1 text-[13px] text-muted hover:border-border-strong hover:text-text sm:inline-flex"
              aria-label="Wallet balance"
            >
              <WalletIcon aria-hidden className="h-3.5 w-3.5" />
              <Money cents={wallet.data.balance_cents} className="font-medium text-text" />
            </Link>
          ) : null}
          {status === "loading" ? (
            <span aria-hidden className="h-8 w-8 rounded-full bg-slate-100" />
          ) : signedIn ? (
            <UserMenu />
          ) : (
            <Button size="sm" onClick={() => void openLogin()}>
              Sign in
            </Button>
          )}
          <Button
            variant="ghost"
            size="icon"
            className="md:hidden"
            aria-label={mobileOpen ? "Close menu" : "Open menu"}
            aria-expanded={mobileOpen}
            aria-controls="mobile-nav"
            onClick={() => setMobileOpen((o) => !o)}
          >
            {mobileOpen ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}
          </Button>
        </div>
      </div>
      {mobileOpen ? (
        <nav id="mobile-nav" aria-label="Main" className="border-t border-border bg-surface md:hidden">
          <div className="page-container grid gap-1 py-2">
            {NAV.map(({ to, label, icon: Icon, end }) => (
              <NavLink key={to} to={to} end={end} className={({ isActive }) => cn(linkClass({ isActive }), "h-11")}>
                <Icon aria-hidden className="h-4 w-4" />
                {label}
              </NavLink>
            ))}
            {signedIn && wallet.data ? (
              <div className="flex items-center justify-between px-3 py-2 text-sm text-muted">
                Balance
                <Money cents={wallet.data.balance_cents} className="font-medium text-text" />
              </div>
            ) : null}
          </div>
        </nav>
      ) : null}
    </header>
  );
}
