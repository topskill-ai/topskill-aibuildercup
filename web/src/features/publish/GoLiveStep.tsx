/** Wizard Step 4: address, accent, model, toggles and a read-only summary before Publish. */
import { BookOpen, Check, ClipboardList, FileText, Loader2, X } from "lucide-react";
import { useMeta } from "@/api/meta";
import type { Skill } from "@/api/types";
import { Avatar } from "@/components/ui/Avatar";
import { Badge } from "@/components/ui/Badge";
import { Card, CardBody } from "@/components/ui/Card";
import { Field, Input } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import { Switch } from "@/components/ui/Switch";
import { formatBytes } from "@/lib/format";
import { FeatureBadges } from "../marketplace/AgentBits";
import { AccentPicker } from "./editors";
import { SLUG_REASON_TEXT, parsePricing, pricingLine, type FieldErrors, type SlugStatus, type WizardForm } from "./wizard";

function SlugHint({ status }: { status: SlugStatus }) {
  switch (status.state) {
    case "auto":
      return <span>Leave blank to generate one from the name.</span>;
    case "checking":
      return (
        <span className="inline-flex items-center gap-1">
          <Loader2 aria-hidden className="h-3.5 w-3.5 animate-spin" /> Checking…
        </span>
      );
    case "available":
      return (
        <span className="inline-flex items-center gap-1 text-success-text">
          <Check aria-hidden className="h-3.5 w-3.5" /> Available
        </span>
      );
    case "unavailable":
      return (
        <span className="inline-flex items-center gap-1 text-danger-text">
          <X aria-hidden className="h-3.5 w-3.5" />
          {status.reason ? SLUG_REASON_TEXT[status.reason] : "Not available."}
        </span>
      );
    case "invalid":
      return <span className="text-danger-text">{status.message}</span>;
    case "error":
      return <span>Could not check the address now; it is checked again when you publish.</span>;
  }
}

export function GoLiveStep({
  form,
  onChange,
  slugStatus,
  errors,
  skill,
  knowledgeFiles,
  fromSample,
  publicBase,
}: {
  form: WizardForm;
  onChange: (next: WizardForm) => void;
  slugStatus: SlugStatus;
  errors: FieldErrors;
  skill: Skill | null;
  knowledgeFiles: File[];
  /** Built from the bundled sample skill: explain why the listing starts off. */
  fromSample?: boolean;
  publicBase: string;
}) {
  const meta = useMeta();
  const g = form.golive;
  const setG = (patch: Partial<WizardForm["golive"]>) => onChange({ ...form, golive: { ...g, ...patch } });
  const pricing = parsePricing(form.pricing).pricing;
  const models = meta.data?.chat_models ?? (g.model ? [g.model] : []);

  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
      <div className="min-w-0 space-y-5">
        <Field
          label="Public address"
          hint={slugStatus.state === "invalid" || slugStatus.state === "unavailable" ? undefined : <SlugHint status={slugStatus} />}
          error={
            slugStatus.state === "invalid" || slugStatus.state === "unavailable" ? <SlugHint status={slugStatus} /> : undefined
          }
        >
          {(c) => (
            <div className="flex min-w-0 items-stretch">
              <span className="hidden items-center rounded-l-control border border-r-0 border-border bg-sunken px-3 font-mono text-[13px] text-muted sm:flex">
                {publicBase}/a/
              </span>
              <Input
                {...c}
                mono
                className="sm:rounded-l-none"
                placeholder="auto"
                value={g.slug}
                maxLength={60}
                autoCapitalize="off"
                spellCheck={false}
                onChange={(e) => setG({ slug: e.target.value.toLowerCase() })}
              />
            </div>
          )}
        </Field>
        <AccentPicker
          value={form.identity.accent_color}
          onChange={(accent_color) => onChange({ ...form, identity: { ...form.identity, accent_color } })}
        />
        <Field label="Model" error={errors.model} hint="Gemini model that answers visitors.">
          {(c) => (
            <Select
              id={c.id}
              value={g.model}
              options={models.map((m) => ({ value: m, label: m === meta.data?.default_chat_model ? `${m} (default)` : m }))}
              onChange={(e) => setG({ model: e.target.value })}
            />
          )}
        </Field>
        <div className="space-y-4 rounded-card border border-border p-4">
          <Switch
            checked={g.web_search_enabled}
            onCheckedChange={(web_search_enabled) => setG({ web_search_enabled })}
            label="Ground answers with Google Search"
            description="For fresh facts not covered by your documents. Sources are shown under answers."
          />
          <Switch
            checked={g.listed}
            onCheckedChange={(listed) => setG({ listed })}
            label="List in the marketplace"
            description={
              fromSample
                ? "Agents built from the sample skill start unlisted so the marketplace stays tidy. They are still reachable by their link and over A2A."
                : "Unlisted agents are still reachable by their link and over A2A."
            }
          />
        </div>
      </div>

      <Card className="h-max">
        <CardBody className="space-y-4">
          <div className="flex items-center gap-3">
            <Avatar
              src={form.identity.avatar?.avatar_url}
              name={form.identity.title || "Agent"}
              accent={form.identity.accent_color}
              size={48}
              ring
            />
            <div className="min-w-0">
              <div className="truncate font-semibold">{form.identity.title || "Untitled agent"}</div>
              <div className="truncate text-sm text-muted">{form.identity.tagline}</div>
            </div>
          </div>
          <div className="space-y-2.5 text-sm">
            <div className="flex gap-2">
              <div className="text-muted">
                {pricing && meta.data ? pricingLine(pricing, meta.data.revenue_share_bp) : "Pricing needs attention."}
              </div>
            </div>
            <div className="flex items-center gap-2">
              <BookOpen aria-hidden className="h-4 w-4 shrink-0 text-subtle" />
              <div className="min-w-0 truncate">
                Skill: <span className="font-medium">{skill?.name ?? "—"}</span>
                {skill ? <span className="text-subtle"> · {skill.files.length} files</span> : null}
              </div>
            </div>
            <div className="flex items-start gap-2">
              <FileText aria-hidden className="mt-0.5 h-4 w-4 shrink-0 text-subtle" />
              <div className="min-w-0">
                {knowledgeFiles.length === 0 ? (
                  <span className="text-muted">No knowledge files. You can add them later.</span>
                ) : (
                  <>
                    {knowledgeFiles.length} knowledge file{knowledgeFiles.length === 1 ? "" : "s"}, uploaded after publish:
                    <ul className="mt-1 space-y-0.5 text-[13px] text-muted">
                      {knowledgeFiles.map((f) => (
                        <li key={`${f.name}:${f.size}`} className="truncate">
                          {f.name} · {formatBytes(f.size)}
                        </li>
                      ))}
                    </ul>
                  </>
                )}
              </div>
            </div>
            <div className="flex items-center gap-2">
              <ClipboardList aria-hidden className="h-4 w-4 shrink-0 text-subtle" />
              <div>
                {form.records.enabled && form.records.fields.length > 0
                  ? `Lead capture: ${form.records.fields.map((f) => f.label).join(", ")}`
                  : "Lead capture off"}
              </div>
            </div>
          </div>
          <div className="flex flex-wrap gap-1.5">
            <FeatureBadges
              features={{
                knowledge: knowledgeFiles.length > 0,
                web_search: g.web_search_enabled,
                records: form.records.enabled && form.records.fields.length > 0,
                a2a: Boolean(pricing?.a2a_enabled),
              }}
            />
            {!g.listed ? <Badge variant="neutral">Unlisted</Badge> : null}
          </div>
        </CardBody>
      </Card>
    </div>
  );
}
