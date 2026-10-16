import { useQuery } from "@tanstack/react-query";
import {
  Bot,
  ChevronDown,
  CreditCard,
  LayoutDashboard,
  LogOut,
  Menu,
  Store,
  Wallet as WalletIcon,
  X,
} from "lucide-react";
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
import { LogoMark } from "./LogoMark";
import { ThemeToggle } from "./ThemeToggle";

const NAV = [
  { to: "/", label: "Marketplace", icon: Store, end: true },
  { to: "/assistant", label: "Assistant", icon: Bot, end: false },
  { to: "/studio", label: "Studio", icon: LayoutDashboard, end: false },
  { to: "/wallet", label: "Wallet", icon: WalletIcon, end: false },
] as const;

function Logo({ className }: { className?: string }) {
  return (
    <Link to="/" className={cn("flex shrink-0 items-center gap-2.5 text-text", className)}>
      <LogoMark />
      <span className="font-display text-[19px] font-bold tracking-display">TopSkill</span>
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
        className="flex items-center gap-1 rounded-chip p-0.5 pr-1 hover:bg-mist"
      >
        <Avatar solid name={name} src={me?.photo_url} size={34} className="border-2 border-surface ring-1 ring-border" />
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
            className="flex items-center gap-2 rounded-control px-3 py-2 text-sm text-text hover:bg-mist"
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
            className="flex w-full items-center gap-2 rounded-control px-3 py-2 text-left text-sm text-text hover:bg-mist"
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
      "inline-flex h-9 items-center gap-1.5 rounded-[9px] px-3.5 text-sm transition-colors duration-150",
      isActive ? "bg-primary-soft font-semibold text-primary" : "font-medium text-muted hover:bg-mist hover:text-text",
    );

  return (
    <header className="sticky top-0 z-30 border-b border-border bg-surface/90 backdrop-blur-md backdrop-saturate-150 supports-[backdrop-filter]:bg-surface/80 dark:bg-bg/85 dark:supports-[backdrop-filter]:bg-bg/80">
      <div className="page-container flex h-16 items-center gap-3">
        <Logo />
        <nav aria-label="Main" className="ml-6 hidden items-center gap-1 md:flex">
          {NAV.map(({ to, label, end }) => (
            <NavLink key={to} to={to} end={end} className={linkClass}>
              {label}
            </NavLink>
          ))}
        </nav>
        <div className="ml-auto flex items-center gap-2 sm:gap-3">
          <ThemeToggle />
          {signedIn && wallet.data ? (
            <Link
              to="/wallet"
              className="hidden items-center gap-2 rounded-chip border border-border bg-surface px-3.5 py-1.5 text-[13.5px] font-semibold text-text-2 shadow-soft transition-[border-color,box-shadow] duration-150 hover:border-faint hover:shadow-lift sm:inline-flex"
              aria-label="Wallet balance"
            >
              <CreditCard aria-hidden className="h-[15px] w-[15px] text-primary" />
              <Money cents={wallet.data.balance_cents} />
            </Link>
          ) : null}
          {status === "loading" ? (
            <span aria-hidden className="h-[34px] w-[34px] rounded-full bg-mist" />
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
