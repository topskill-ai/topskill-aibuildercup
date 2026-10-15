/**
 * Agent detail (SPEC §11.4): header with status and "Update version", and tabs
 * Overview · Settings · Knowledge · Records · Versions · Share (`?tab=`).
 */
import { Bot, ExternalLink, Rocket } from "lucide-react";
import { useState } from "react";
import { Link, useParams, useSearchParams } from "react-router";
import { hasCode, ErrorCodes } from "@/api/errors";
import { PageBody, PageHeader, PageLoading } from "@/components/layout/PageHeader";
import { Avatar } from "@/components/ui/Avatar";
import { Badge } from "@/components/ui/Badge";
import { Button, buttonClasses } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorLine } from "@/components/ui/ErrorLine";
import { TabPanel, Tabs } from "@/components/ui/Tabs";
import { cn } from "@/lib/cn";
import { KnowledgeTab } from "@/features/knowledge/KnowledgeTab";
import { RecordsTab } from "@/features/records/RecordsTab";
import { useAgent } from "@/features/studio/api";
import { KnowledgeBadge, OverviewTab } from "@/features/studio/OverviewTab";
import { SettingsTab } from "@/features/studio/SettingsTab";
import { ShareTab } from "@/features/studio/ShareTab";
import { UpdateVersionDialog, VersionsTab } from "@/features/studio/VersionsTab";

const TABS = ["overview", "settings", "knowledge", "records", "versions", "share"] as const;
type Tab = (typeof TABS)[number];
const LABELS: Record<Tab, string> = {
  overview: "Overview",
  settings: "Settings",
  knowledge: "Knowledge",
  records: "Records",
  versions: "Versions",
  share: "Share",
};

function isTab(v: string | null): v is Tab {
  return v !== null && (TABS as readonly string[]).includes(v);
}

export default function AgentDetailPage() {
  const { id = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const tab: Tab = isTab(params.get("tab")) ? (params.get("tab") as Tab) : "overview";
  const [updateOpen, setUpdateOpen] = useState(false);
  const q = useAgent(id);
  const agent = q.data;

  const setTab = (t: Tab) =>
    setParams(
      (p) => {
        const n = new URLSearchParams(p);
        n.set("tab", t);
        return n;
      },
      { replace: true },
    );

  if (q.isPending) return <PageLoading />;
  if (!agent) {
    return (
      <PageBody>
        {hasCode(q.error, ErrorCodes.agent_not_found, ErrorCodes.not_found) ? (
          <EmptyState
            icon={<Bot />}
            title="Agent not found"
            description="It may have been deleted, or it belongs to another account."
            action={
              <Link to="/studio" className={buttonClasses({ variant: "outline" })}>
                Back to Studio
              </Link>
            }
          />
        ) : (
          <ErrorLine error={q.error} onRetry={() => void q.refetch()} />
        )}
      </PageBody>
    );
  }

  const idBase = `agent-${agent.id}`;

  return (
    <PageBody>
      <PageHeader
        back={{ to: "/studio", label: "Studio" }}
        leading={<Avatar src={agent.avatar_url} name={agent.title} accent={agent.accent_color} size={56} ring />}
        title={agent.title}
        description={
          <span className="flex flex-wrap items-center gap-2">
            <Badge dot variant={agent.status === "active" ? "success" : "neutral"}>
              {agent.status === "active" ? "Active" : "Paused"}
            </Badge>
            <span className="font-mono text-[13px] text-subtle">/a/{agent.slug}</span>
            <KnowledgeBadge agent={agent} onOpen={() => setTab("knowledge")} />
          </span>
        }
        actions={
          <>
            <a
              href={agent.urls.public_page}
              target="_blank"
              rel="noreferrer"
              className={buttonClasses({ variant: "outline" })}
            >
              <ExternalLink aria-hidden className="h-4 w-4" />
              Open public page
            </a>
            <Button
              variant={agent.config_dirty ? "primary" : "outline"}
              icon={<Rocket className="h-4 w-4" />}
              className={cn(agent.config_dirty && "ring-2 ring-primary/30 ring-offset-2")}
              onClick={() => setUpdateOpen(true)}
            >
              Update version
            </Button>
          </>
        }
      />
      {agent.config_dirty ? (
        <div className="mb-4 rounded-card border border-warning/30 bg-warning-soft px-4 py-2.5 text-sm text-warning">
          Runtime changes are saved but not live yet. Update the version to apply them.
        </div>
      ) : null}

      <Tabs
        idBase={idBase}
        aria-label="Agent sections"
        value={tab}
        onValueChange={setTab}
        items={TABS.map((t) => ({
          value: t,
          label: LABELS[t],
          badge:
            t === "knowledge" && agent.knowledge.files > 0
              ? agent.knowledge.files
              : t === "records" && agent.stats.records > 0
                ? agent.stats.records
                : undefined,
        }))}
      />
      <TabPanel idBase={idBase} value={tab}>
        {tab === "overview" ? (
          <OverviewTab agent={agent} onOpenTab={setTab} />
        ) : tab === "settings" ? (
          <SettingsTab key={agent.id} agent={agent} onUpdateVersion={() => setUpdateOpen(true)} />
        ) : tab === "knowledge" ? (
          <KnowledgeTab agent={agent} />
        ) : tab === "records" ? (
          <RecordsTab agent={agent} onOpenSettings={() => setTab("settings")} />
        ) : tab === "versions" ? (
          <VersionsTab agent={agent} onUpdate={() => setUpdateOpen(true)} />
        ) : (
          <ShareTab agent={agent} />
        )}
      </TabPanel>

      <UpdateVersionDialog agent={agent} open={updateOpen} onOpenChange={setUpdateOpen} />
    </PageBody>
  );
}
