/** Wizard Step 2: identity fields prefilled from the Gemini draft, avatar and lead capture. */
import { ChevronDown, ClipboardList, RefreshCw, Sparkles } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/Button";
import { Field, Input } from "@/components/ui/Input";
import { Skeleton } from "@/components/ui/Skeleton";
import { Switch } from "@/components/ui/Switch";
import { Textarea } from "@/components/ui/Textarea";
import { cn } from "@/lib/cn";
import { RecordFieldsEditor } from "@/features/records/RecordFieldsEditor";
import { AvatarPicker } from "./AvatarPicker";
import { CapabilitiesEditor, StarterQuestionsEditor } from "./editors";
import { LIMITS, type FieldErrors, type IdentityForm, type RecordsForm } from "./wizard";

export function DraftingSkeleton({ skillName, quality }: { skillName: string; quality: "fast" | "best" }) {
  return (
    <div role="status" aria-live="polite" className="space-y-5">
      <div className="flex items-center gap-3 rounded-card border border-primary/20 bg-primary-soft px-4 py-3">
        <Sparkles aria-hidden className="h-4 w-4 animate-pulse text-primary" />
        <p className="text-sm font-medium text-primary">
          Drafting with Gemini{quality === "best" ? " Pro" : ""}… reading {skillName || "your skill"} and writing the
          listing, persona, pricing and starter questions.
        </p>
      </div>
      <div className="flex items-center gap-4">
        <Skeleton className="h-[72px] w-[72px] rounded-full" />
        <div className="flex-1 space-y-2">
          <Skeleton className="h-4 w-40" />
          <Skeleton className="h-8 w-56 max-w-full" />
        </div>
      </div>
      {["Name", "Tagline", "Description", "Persona", "Greeting", "Starter questions"].map((label, i) => (
        <div key={label}>
          <div className="mb-1.5 text-sm font-medium text-subtle">{label}</div>
          <Skeleton className={cn("w-full", i === 2 || i === 3 ? "h-24" : "h-10")} />
        </div>
      ))}
    </div>
  );
}

export function IdentityStep({
  identity,
  records,
  errors,
  onIdentity,
  onRecords,
  onRedraft,
  redrafting,
  canRedraft,
  onAvatarBusy,
}: {
  identity: IdentityForm;
  records: RecordsForm;
  errors: FieldErrors;
  onIdentity: (next: IdentityForm) => void;
  onRecords: (next: RecordsForm) => void;
  onRedraft: (quality: "fast" | "best") => void;
  redrafting: boolean;
  canRedraft: boolean;
  onAvatarBusy: (busy: boolean) => void;
}) {
  const [leadOpen, setLeadOpen] = useState(records.enabled);
  // A lead-capture error must be visible, so the section opens itself while one is shown.
  const leadExpanded = leadOpen || Boolean(errors.records);
  const set = (patch: Partial<IdentityForm>) => onIdentity({ ...identity, ...patch });

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <AvatarPicker
          value={identity.avatar}
          title={identity.title}
          accent={identity.accent_color}
          prompt={identity.avatar_prompt}
          onChange={(avatar) => set({ avatar })}
          onBusyChange={onAvatarBusy}
        />
        {canRedraft ? (
          <div className="flex flex-wrap gap-2">
            <Button
              variant="outline"
              size="sm"
              icon={<RefreshCw className="h-4 w-4" />}
              disabled={redrafting}
              onClick={() => onRedraft("fast")}
            >
              Redraft
            </Button>
            <Button
              variant="ghost"
              size="sm"
              icon={<Sparkles className="h-4 w-4" />}
              disabled={redrafting}
              onClick={() => onRedraft("best")}
            >
              Better draft (Pro)
            </Button>
          </div>
        ) : null}
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <Field label="Name" error={errors.title}>
          {(c) => (
            <Input {...c} value={identity.title} maxLength={LIMITS.title} onChange={(e) => set({ title: e.target.value })} />
          )}
        </Field>
        <Field label="Tagline" error={errors.tagline}>
          {(c) => (
            <Input
              {...c}
              value={identity.tagline}
              maxLength={LIMITS.tagline}
              onChange={(e) => set({ tagline: e.target.value })}
            />
          )}
        </Field>
      </div>
      <Field label="Description" error={errors.description}>
        {(c) => (
          <Textarea
            {...c}
            autoResize
            maxRows={8}
            value={identity.description}
            maxLength={LIMITS.description}
            onChange={(e) => set({ description: e.target.value })}
          />
        )}
      </Field>
      <Field
        label="Persona"
        hint="The agent's instructions: who it serves, its workflow and its boundaries."
        error={errors.soul}
      >
        {(c) => (
          <Textarea
            {...c}
            autoResize
            maxRows={14}
            rows={6}
            value={identity.soul}
            maxLength={LIMITS.soul}
            onChange={(e) => set({ soul: e.target.value })}
          />
        )}
      </Field>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <Field label="Greeting">
          {(c) => (
            <Textarea
              {...c}
              autoResize
              rows={2}
              maxRows={4}
              value={identity.greeting}
              maxLength={LIMITS.greeting}
              onChange={(e) => set({ greeting: e.target.value })}
            />
          )}
        </Field>
        <Field label="Input placeholder">
          {(c) => (
            <Input
              {...c}
              value={identity.input_placeholder}
              maxLength={LIMITS.placeholder}
              onChange={(e) => set({ input_placeholder: e.target.value })}
            />
          )}
        </Field>
      </div>
      <StarterQuestionsEditor
        value={identity.starter_questions}
        errors={errors}
        onChange={(starter_questions) => set({ starter_questions })}
      />
      <CapabilitiesEditor
        value={identity.capabilities}
        errors={errors}
        onChange={(capabilities) => set({ capabilities })}
      />

      <div className="rounded-card border border-border">
        <button
          type="button"
          aria-expanded={leadExpanded}
          onClick={() => setLeadOpen(!leadExpanded)}
          className="flex w-full items-center gap-3 px-4 py-3 text-left"
        >
          <ClipboardList aria-hidden className="h-4 w-4 text-primary" />
          <span className="flex-1">
            <span className="block text-sm font-semibold">Lead capture</span>
            <span className="block text-[13px] text-muted">
              {records.enabled
                ? `The agent fills ${records.fields.length} field${records.fields.length === 1 ? "" : "s"} while it chats. You see them in Records.`
                : "Off. Turn on to collect structured details from visitors."}
            </span>
          </span>
          <ChevronDown aria-hidden className={cn("h-4 w-4 text-subtle transition-transform", leadExpanded ? "" : "-rotate-90")} />
        </button>
        {leadExpanded ? (
          <div className="space-y-4 border-t border-border p-4">
            <Switch
              checked={records.enabled}
              onCheckedChange={(enabled) => onRecords({ ...records, enabled })}
              label="Collect a record in every conversation"
            />
            {records.enabled ? (
              <RecordFieldsEditor value={records.fields} onChange={(fields) => onRecords({ ...records, fields })} />
            ) : null}
            {errors.records ? (
              <p className="text-[13px] text-danger" data-step-error tabIndex={-1}>
                {errors.records}
              </p>
            ) : null}
          </div>
        ) : null}
      </div>
    </div>
  );
}
