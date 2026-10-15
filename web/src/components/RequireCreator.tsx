import { useMutation } from "@tanstack/react-query";
import { BadgeCheck, Coins, Network, Sparkles } from "lucide-react";
import { useState, type ReactNode } from "react";
import { Navigate, Outlet, useLocation } from "react-router";
import { api } from "@/api/client";
import type { Me } from "@/api/types";
import { useAuth } from "@/lib/auth";
import { loginPath } from "@/lib/nav";
import { PageLoading } from "./layout/PageHeader";
import { Button } from "./ui/Button";
import { Card, CardBody } from "./ui/Card";
import { ErrorLine } from "./ui/ErrorLine";
import { Field, Input } from "./ui/Input";

const PERKS = [
  { icon: Sparkles, text: "Gemini drafts your agent listing from a SKILL.md in seconds." },
  { icon: Coins, text: "Earn a revenue share on every paid round and A2A call." },
  { icon: Network, text: "Other AI agents can hire your agent over A2A." },
];

function BecomeCreatorCard() {
  const { setMe, me } = useAuth();
  const [headline, setHeadline] = useState(me?.creator_profile?.headline ?? "");
  const mutation = useMutation({
    mutationFn: () => api.post<Me>("/api/me/become-creator", headline.trim() ? { headline: headline.trim() } : {}),
    onSuccess: (next) => setMe(next),
  });

  return (
    <div className="page-container py-10 sm:py-16">
      <Card className="mx-auto max-w-lg">
        <CardBody className="p-6 sm:p-8">
          <div className="mb-4 flex h-11 w-11 items-center justify-center rounded-full bg-primary-soft text-primary">
            <BadgeCheck aria-hidden className="h-5 w-5" />
          </div>
          <h1 className="text-xl font-semibold">Become a creator</h1>
          <p className="mt-1 text-sm text-muted">
            The studio is for creators. Turn your playbook into a paid AI agent that people and other agents can use.
          </p>
          <ul className="mt-5 space-y-2.5">
            {PERKS.map(({ icon: Icon, text }) => (
              <li key={text} className="flex items-start gap-2.5 text-sm text-text">
                <Icon aria-hidden className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
                {text}
              </li>
            ))}
          </ul>
          <form
            className="mt-6 space-y-4"
            onSubmit={(e) => {
              e.preventDefault();
              mutation.mutate();
            }}
          >
            <Field label="Headline (optional)" hint="Shown on your agents' pages, e.g. “Immigration consultant”.">
              {(p) => (
                <Input
                  {...p}
                  value={headline}
                  maxLength={80}
                  onChange={(e) => setHeadline(e.target.value)}
                  placeholder="Immigration consultant"
                />
              )}
            </Field>
            {mutation.isError ? <ErrorLine error={mutation.error} /> : null}
            <Button type="submit" size="lg" className="w-full" loading={mutation.isPending}>
              Become a creator
            </Button>
          </form>
        </CardBody>
      </Card>
    </div>
  );
}

/**
 * Route guard for `/studio/*` (SPEC §11.2): signed-out users go to `/login?next=…`; visitors see a
 * "Become a creator" card (`POST /api/me/become-creator`); creators get the page.
 */
export function RequireCreator({ children }: { children?: ReactNode }) {
  const { status, me, meError, refetchMe } = useAuth();
  const location = useLocation();
  if (status === "loading") return <PageLoading />;
  if (status === "signed_out") {
    return <Navigate to={loginPath(location.pathname, location.search, location.hash)} replace />;
  }
  if (!me) {
    return (
      <div className="page-container py-10">
        <ErrorLine error={meError} message={meError ? undefined : "Could not load your profile."} onRetry={refetchMe} />
      </div>
    );
  }
  if (me.role !== "creator") return <BecomeCreatorCard />;
  return <>{children ?? <Outlet />}</>;
}
