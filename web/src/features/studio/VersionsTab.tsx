/**
 * Versions: "Update version" (rebuild from the current config, MUST) and the version list with
 * rollback (SHOULD; hidden when the list endpoint is not available).
 */
import { useMutation } from "@tanstack/react-query";
import { GitCommitHorizontal, History, Rocket } from "lucide-react";
import { useState } from "react";
import { isApiError } from "@/api/errors";
import type { Agent } from "@/api/types";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { Dialog } from "@/components/ui/Dialog";
import { ErrorLine } from "@/components/ui/ErrorLine";
import { Field, Input } from "@/components/ui/Input";
import { SkeletonRows } from "@/components/ui/Skeleton";
import { cn } from "@/lib/cn";
import { formatDateTime } from "@/lib/format";
import { toast, toastApiError } from "@/lib/toast";
import { agentApi, useAgentCacheUpdater, useVersions } from "./api";

export function UpdateVersionDialog({
  agent,
  open,
  onOpenChange,
}: {
  agent: Agent;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const [note, setNote] = useState("");
  const update = useAgentCacheUpdater();
  // POST /versions sets status=active (SPEC §6.4), so updating a paused agent also resumes it.
  const resumes = agent.status === "paused";
  const m = useMutation({
    mutationFn: () => agentApi.newVersion(agent.id, note),
    onSuccess: (a) => {
      update(a);
      toast.success(resumes ? `Version ${a.active_version} is live and the agent is active again` : `Version ${a.active_version} is live`);
      setNote("");
      onOpenChange(false);
    },
  });
  const submit = () => {
    if (!m.isPending) m.mutate();
  };
  return (
    <Dialog
      open={open}
      onOpenChange={(o) => !m.isPending && onOpenChange(o)}
      dismissible={!m.isPending}
      title="Update version"
      description="Builds a new version from the current persona, model, skills and lead capture settings and makes it live."
      footer={
        <>
          <Button variant="outline" disabled={m.isPending} onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button icon={<Rocket className="h-4 w-4" />} loading={m.isPending} onClick={submit}>
            {resumes ? "Update and resume" : `Update to version ${agent.version_count + 1}`}
          </Button>
        </>
      }
    >
      <Field label="What changed? (optional)">
        {(c) => (
          <Input
            {...c}
            data-autofocus
            value={note}
            maxLength={200}
            placeholder="e.g. Shorter answers, new checklist"
            disabled={m.isPending}
            onChange={(e) => setNote(e.target.value)}
            onKeyDown={(e) => {
              if (e.key !== "Enter" || e.nativeEvent.isComposing) return;
              e.preventDefault();
              submit();
            }}
          />
        )}
      </Field>
      {resumes ? (
        <p className="mt-3 rounded-control border border-warning/30 bg-warning-soft px-3 py-2 text-[13px] text-warning" role="note">
          This also resumes the agent: visitors and AI agents can use it again.
        </p>
      ) : null}
      {m.error ? <ErrorLine className="mt-3" error={m.error} /> : null}
    </Dialog>
  );
}

export function VersionsTab({ agent, onUpdate }: { agent: Agent; onUpdate: () => void }) {
  const versions = useVersions(agent.id);
  const update = useAgentCacheUpdater();
  const activate = useMutation({
    mutationFn: (n: number) => agentApi.activate(agent.id, n),
    onSuccess: (a) => {
      update(a);
      toast.success(`Version ${a.active_version} is live`);
    },
    onError: (err) => toastApiError(err, "Could not switch versions"),
  });
  const listMissing = isApiError(versions.error) && versions.error.status === 404 && versions.error.code !== "agent_not_found";

  return (
    <div className="space-y-6">
      <Card className={cn(agent.config_dirty && "border-warning/40 bg-warning-soft/40")}>
        <CardBody className="flex flex-wrap items-center gap-4">
          <GitCommitHorizontal aria-hidden className="h-5 w-5 shrink-0 text-primary" />
          <div className="min-w-0 flex-1">
            <div className="font-semibold">Version {agent.active_version} is live</div>
            <p className="text-sm text-muted">
              {agent.config_dirty
                ? "You changed the persona, model, skills or lead capture. Update the version to make the changes live."
                : "Live fields (name, pricing, greeting, knowledge files) apply immediately. Runtime changes need a new version."}
            </p>
          </div>
          <Button variant={agent.config_dirty ? "primary" : "outline"} icon={<Rocket className="h-4 w-4" />} onClick={onUpdate}>
            Update version
          </Button>
        </CardBody>
      </Card>

      {listMissing ? null : (
        <Card>
          <CardHeader title="History" description="Roll back to an earlier version at any time. Your settings stay as they are." />
          <div className="pt-3">
            {versions.isPending ? (
              <SkeletonRows rows={3} className="p-5" />
            ) : versions.error ? (
              <ErrorLine className="p-5" error={versions.error} onRetry={() => void versions.refetch()} />
            ) : (
              <ul className="divide-y divide-border border-t border-border">
                {versions.data.items.map((v) => (
                  <li key={v.n} className="flex flex-wrap items-center gap-3 px-5 py-3">
                    <History aria-hidden className="h-4 w-4 shrink-0 text-subtle" />
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-medium">Version {v.n}</span>
                        {v.active ? (
                          <Badge variant="success" dot>
                            Live
                          </Badge>
                        ) : null}
                        <span className="font-mono text-xs text-subtle">{v.model}</span>
                      </div>
                      <div className="mt-0.5 truncate text-[13px] text-muted">
                        {formatDateTime(v.created_at)}
                        {v.note ? ` · ${v.note}` : ""}
                        {v.skills.length ? ` · ${v.skills.map((s) => s.name).join(", ")}` : ""}
                        {v.web_search_enabled ? " · web search" : ""}
                        {v.records_enabled ? " · lead capture" : ""}
                      </div>
                    </div>
                    {!v.active ? (
                      <Button
                        variant="outline"
                        size="sm"
                        loading={activate.isPending && activate.variables === v.n}
                        disabled={activate.isPending}
                        onClick={() => activate.mutate(v.n)}
                      >
                        Activate
                      </Button>
                    ) : null}
                  </li>
                ))}
              </ul>
            )}
          </div>
        </Card>
      )}
    </div>
  );
}
