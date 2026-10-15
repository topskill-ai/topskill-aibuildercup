/**
 * Sign-in UI (SPEC §11.5): email/password sign in + create account, Google (VITE_GOOGLE_SIGNIN=1)
 * and Developer login (VITE_DEV_AUTH=1).
 *
 * Visitor pages never redirect on 401: they call `ensureSignedIn()` from `useLoginDialog()`,
 * which opens this dialog and resolves `true` once signed in (then the page resumes the action).
 */
import { Code2 } from "lucide-react";
import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
  useState,
  type FormEvent,
  type ReactNode,
} from "react";
import type { Me, Role } from "@/api/types";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/cn";
import { Button } from "./ui/Button";
import { Dialog } from "./ui/Dialog";
import { ErrorLine } from "./ui/ErrorLine";
import { Field, Input } from "./ui/Input";
import { Select } from "./ui/Select";

type Mode = "sign_in" | "sign_up";

export interface LoginFormProps {
  /** Called after a successful sign-in with the loaded profile. */
  onSuccess: (me: Me) => void;
  initialMode?: Mode;
  className?: string;
}

function GoogleIcon() {
  return (
    <svg aria-hidden viewBox="0 0 24 24" className="h-4 w-4">
      <path
        fill="#4285F4"
        d="M23.5 12.27c0-.85-.08-1.67-.22-2.45H12v4.64h6.45a5.52 5.52 0 0 1-2.4 3.62v3h3.88c2.27-2.09 3.57-5.17 3.57-8.81Z"
      />
      <path
        fill="#34A853"
        d="M12 24c3.24 0 5.96-1.07 7.94-2.92l-3.88-3c-1.08.72-2.45 1.15-4.06 1.15-3.12 0-5.77-2.11-6.71-4.95H1.28v3.1A12 12 0 0 0 12 24Z"
      />
      <path
        fill="#FBBC05"
        d="M5.29 14.28A7.2 7.2 0 0 1 4.91 12c0-.79.14-1.56.38-2.28v-3.1H1.28A12 12 0 0 0 0 12c0 1.94.46 3.77 1.28 5.38l4.01-3.1Z"
      />
      <path
        fill="#EA4335"
        d="M12 4.77c1.76 0 3.34.6 4.58 1.79l3.44-3.44A11.5 11.5 0 0 0 12 0 12 12 0 0 0 1.28 6.62l4.01 3.1C6.23 6.88 8.88 4.77 12 4.77Z"
      />
    </svg>
  );
}

/** The sign-in form itself; used by the dialog and by `/login`. */
export function LoginForm({ onSuccess, initialMode = "sign_in", className }: LoginFormProps) {
  const auth = useAuth();
  const [mode, setMode] = useState<Mode>(initialMode);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [devUid, setDevUid] = useState("");
  const [devRole, setDevRole] = useState<Role>("visitor");
  const [busy, setBusy] = useState<null | "email" | "google" | "dev">(null);
  const [error, setError] = useState<unknown>(null);

  const run = async (kind: "email" | "google" | "dev", fn: () => Promise<Me>) => {
    setBusy(kind);
    setError(null);
    try {
      const me = await fn();
      onSuccess(me);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(null);
    }
  };

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (busy) return;
    if (mode === "sign_up" && password.length < 6) {
      setError(new Error("Choose a password with at least 6 characters."));
      return;
    }
    void run("email", () =>
      mode === "sign_in" ? auth.signIn(email, password) : auth.signUp(email, password, name),
    );
  };

  const onDevSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (busy) return;
    void run("dev", () => auth.devSignIn(devUid, devRole));
  };

  return (
    <div className={cn("min-w-0", className)}>
      <div role="tablist" aria-label="Sign in or create an account" className="mb-5 grid grid-cols-2 rounded-control bg-slate-100 p-1">
        {(
          [
            ["sign_in", "Sign in"],
            ["sign_up", "Create account"],
          ] as const
        ).map(([value, label]) => (
          <button
            key={value}
            type="button"
            role="tab"
            aria-selected={mode === value}
            onClick={() => {
              setMode(value);
              setError(null);
            }}
            className={cn(
              "h-8 rounded-[6px] text-sm font-medium transition-colors duration-150",
              mode === value ? "bg-surface text-text shadow-sm" : "text-muted hover:text-text",
            )}
          >
            {label}
          </button>
        ))}
      </div>

      {auth.googleEnabled ? (
        <>
          <Button
            variant="outline"
            size="lg"
            className="w-full"
            icon={<GoogleIcon />}
            loading={busy === "google"}
            disabled={busy !== null && busy !== "google"}
            onClick={() => void run("google", auth.signInWithGoogle)}
          >
            Continue with Google
          </Button>
          <div className="my-4 flex items-center gap-3 text-xs text-subtle">
            <span className="h-px flex-1 bg-border" />
            or with email
            <span className="h-px flex-1 bg-border" />
          </div>
        </>
      ) : null}

      <form onSubmit={onSubmit} className="space-y-4" noValidate>
        {mode === "sign_up" ? (
          <Field label="Your name">
            {(p) => (
              <Input
                {...p}
                autoComplete="name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Ana Silva"
                maxLength={60}
              />
            )}
          </Field>
        ) : null}
        <Field label="Email">
          {(p) => (
            <Input
              {...p}
              type="email"
              autoComplete="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@company.com"
              data-autofocus
            />
          )}
        </Field>
        <Field label="Password" hint={mode === "sign_up" ? "At least 6 characters." : undefined}>
          {(p) => (
            <Input
              {...p}
              type="password"
              autoComplete={mode === "sign_in" ? "current-password" : "new-password"}
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          )}
        </Field>
        {error && busy === null ? <ErrorLine error={error} /> : null}
        <Button
          type="submit"
          size="lg"
          className="w-full"
          loading={busy === "email"}
          disabled={(busy !== null && busy !== "email") || !email.trim() || !password}
        >
          {mode === "sign_in" ? "Sign in" : "Create account"}
        </Button>
      </form>

      {auth.devAuthEnabled ? (
        <form onSubmit={onDevSubmit} className="mt-6 rounded-card border border-dashed border-warning/50 bg-warning-soft/60 p-4">
          <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-warning">
            <Code2 aria-hidden className="h-4 w-4" />
            Developer login
          </div>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-[minmax(0,1fr)_140px]">
            <Field label="User id">
              {(p) => (
                <Input
                  {...p}
                  mono
                  value={devUid}
                  onChange={(e) => setDevUid(e.target.value)}
                  placeholder="creator-ana"
                  autoComplete="off"
                  spellCheck={false}
                />
              )}
            </Field>
            <Field label="Role">
              {(p) => (
                <Select
                  id={p.id}
                  value={devRole}
                  onChange={(e) => setDevRole(e.target.value as Role)}
                  options={[
                    { value: "visitor", label: "Visitor" },
                    { value: "creator", label: "Creator" },
                  ]}
                />
              )}
            </Field>
          </div>
          <Button
            type="submit"
            variant="outline"
            className="mt-3 w-full"
            loading={busy === "dev"}
            disabled={(busy !== null && busy !== "dev") || !devUid.trim()}
          >
            Sign in as developer
          </Button>
          <p className="mt-2 text-xs text-muted">Local E2E only. Sends X-Dev-* headers; ignored in production.</p>
        </form>
      ) : null}
    </div>
  );
}

/* ---------------------------------------------------------------------------------------------- */

export interface OpenLoginOptions {
  /** Short reason shown under the title, e.g. "Sign in to chat with Visa Advisor". */
  reason?: string;
  mode?: Mode;
}

interface LoginDialogContextValue {
  /** Open the dialog; resolves true when the user signed in, false when they closed it. */
  openLogin: (opts?: OpenLoginOptions) => Promise<boolean>;
  /** Resolves true immediately when already signed in, else behaves like `openLogin`. */
  ensureSignedIn: (opts?: OpenLoginOptions) => Promise<boolean>;
  closeLogin: () => void;
}

const LoginDialogContext = createContext<LoginDialogContextValue | null>(null);

export function LoginDialogProvider({ children }: { children: ReactNode }) {
  const auth = useAuth();
  const [open, setOpen] = useState(false);
  const [opts, setOpts] = useState<OpenLoginOptions>({});
  const resolver = useRef<((ok: boolean) => void) | null>(null);

  const settle = useCallback((ok: boolean) => {
    resolver.current?.(ok);
    resolver.current = null;
  }, []);

  const openLogin = useCallback(
    (next: OpenLoginOptions = {}) => {
      settle(false); // a previous pending open resolves as cancelled
      setOpts(next);
      setOpen(true);
      return new Promise<boolean>((resolve) => {
        resolver.current = resolve;
      });
    },
    [settle],
  );

  const status = auth.status;
  const ensureSignedIn = useCallback(
    (next?: OpenLoginOptions) => (status === "signed_in" ? Promise.resolve(true) : openLogin(next)),
    [status, openLogin],
  );

  const closeLogin = useCallback(() => {
    setOpen(false);
    settle(false);
  }, [settle]);

  const value = useMemo(() => ({ openLogin, ensureSignedIn, closeLogin }), [openLogin, ensureSignedIn, closeLogin]);

  return (
    <LoginDialogContext.Provider value={value}>
      {children}
      <Dialog
        open={open}
        onOpenChange={(o) => (o ? setOpen(true) : closeLogin())}
        title={opts.mode === "sign_up" ? "Create your TopSkill account" : "Sign in to TopSkill"}
        description={opts.reason ?? "New accounts start with free demo credits."}
        size="sm"
      >
        <LoginForm
          key={open ? "open" : "closed"}
          initialMode={opts.mode}
          onSuccess={() => {
            setOpen(false);
            settle(true);
          }}
        />
      </Dialog>
    </LoginDialogContext.Provider>
  );
}

// eslint-disable-next-line react-refresh/only-export-components
export function useLoginDialog(): LoginDialogContextValue {
  const ctx = useContext(LoginDialogContext);
  if (!ctx) throw new Error("useLoginDialog must be used inside <LoginDialogProvider>");
  return ctx;
}
