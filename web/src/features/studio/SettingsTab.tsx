/**
 * Agent Settings: profile (live), runtime config (applies after "Update version"), pricing (live)
 * and status (pause / resume / delete). Each section saves on its own.
 */
import { useMutation } from "@tanstack/react-query";
import { AlertTriangle, Pause, Play, Rocket, Save, Trash2 } from "lucide-react";
import { useState, type ReactNode } from "react";
import { useNavigate } from "react-router";
import { hasCode, ErrorCodes } from "@/api/errors";
import { useMeta } from "@/api/meta";
import type { Agent } from "@/api/types";
import { Button } from "@/components/ui/Button";
import { Card, CardBody, CardFooter, CardHeader } from "@/components/ui/Card";
import { Dialog } from "@/components/ui/Dialog";
import { ErrorLine } from "@/components/ui/ErrorLine";
import { Field, Input } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import { Switch } from "@/components/ui/Switch";
import { Textarea } from "@/components/ui/Textarea";
import { toast } from "@/lib/toast";
import { AvatarPicker } from "@/features/publish/AvatarPicker";
import { AccentPicker, CapabilitiesEditor, StarterQuestionsEditor } from "@/features/publish/editors";
import { PricingFields } from "@/features/publish/PricingFields";
import { LIMITS, parsePricing, pricingFormFromPricing } from "@/features/publish/wizard";
import { RecordFieldsEditor } from "@/features/records/RecordFieldsEditor";
import { agentApi, useAgentCacheUpdater, type UpdateAgentRequest } from "./api";
import {
  profileFromAgent,
  profilePatch,
  runtimeFromAgent,
  runtimePatch,
  temperatureOptions,
  validateProfile,
  validateRuntime,
} from "./settings";

function useSave(agent: Agent, onSaved: (a: Agent) => void) {
  const update = useAgentCacheUpdater();
  return useMutation({
    mutationFn: (body: UpdateAgentRequest) => agentApi.update(agent.id, body),
    onSuccess: (a) => {
      update(a);
      onSaved(a);
    },
  });
}

function SaveBar({
  dirty,
  saving,
  error,
  onSave,
  onReset,
  note,
}: {
  dirty: boolean;
  saving: boolean;
  error: unknown;
  onSave: () => void;
  onReset: () => void;
  note?: ReactNode;
}) {
  return (
    <CardFooter className="justify-between">
      <div className="min-w-0 flex-1">
        {error ? <ErrorLine error={error} /> : note ? <p className="text-[13px] text-muted">{note}</p> : null}
      </div>
      <div className="flex gap-2">
        <Button variant="ghost" disabled={!dirty || saving} onClick={onReset}>
          Discard
        </Button>
        <Button icon={<Save className="h-4 w-4" />} loading={saving} disabled={!dirty} onClick={onSave}>
          Save
        </Button>
      </div>
    </CardFooter>
  );
}

function ProfileSection({ agent }: { agent: Agent }) {
  const [initial, setInitial] = useState(() => profileFromAgent(agent));
  const [p, setP] = useState(initial);
  const [showErrors, setShowErrors] = useState(false);
  const [avatarBusy, setAvatarBusy] = useState(false);
  const save = useSave(agent, (a) => {
    const next = profileFromAgent(a);
    setInitial(next);
    setP(next);
    setShowErrors(false);
    toast.success("Profile saved. Changes are live.");
  });
  const patch = profilePatch(initial, p);
  const errors = validateProfile(p);
  const shown = showErrors ? errors : {};
  const set = (patch: Partial<typeof p>) => setP((cur) => ({ ...cur, ...patch }));

  return (
    <Card>
      <CardHeader title="Profile" description="What visitors see. Changes apply immediately." />
      <CardBody className="space-y-5">
        <AvatarPicker
          value={p.avatar}
          title={p.title}
          accent={p.accent_color}
          prompt={`${p.title}. ${p.tagline}`}
          onChange={(avatar) => set({ avatar })}
          onBusyChange={setAvatarBusy}
        />
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Field label="Name" error={shown.title}>
            {(c) => <Input {...c} value={p.title} maxLength={LIMITS.title} onChange={(e) => set({ title: e.target.value })} />}
          </Field>
          <Field label="Tagline">
            {(c) => (
              <Input {...c} value={p.tagline} maxLength={LIMITS.tagline} onChange={(e) => set({ tagline: e.target.value })} />
            )}
          </Field>
        </div>
        <Field label="Description">
          {(c) => (
            <Textarea
              {...c}
              autoResize
              maxRows={8}
              value={p.description}
              maxLength={LIMITS.description}
              onChange={(e) => set({ description: e.target.value })}
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
                value={p.greeting}
                maxLength={LIMITS.greeting}
                onChange={(e) => set({ greeting: e.target.value })}
              />
            )}
          </Field>
          <Field label="Input placeholder">
            {(c) => (
              <Input
                {...c}
                value={p.input_placeholder}
                maxLength={LIMITS.placeholder}
                onChange={(e) => set({ input_placeholder: e.target.value })}
              />
            )}
          </Field>
        </div>
        {p.starter_questions === null ? (
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-control border border-border p-3 text-sm">
            <span className="text-muted">Starter questions are generated automatically from the capabilities.</span>
            <Button variant="outline" size="sm" onClick={() => set({ starter_questions: [{ label: "", prompt: "" }] })}>
              Write my own
            </Button>
          </div>
        ) : (
          <StarterQuestionsEditor
            value={p.starter_questions}
            errors={shown}
            onChange={(starter_questions) => set({ starter_questions })}
          />
        )}
        <CapabilitiesEditor value={p.capabilities} errors={shown} onChange={(capabilities) => set({ capabilities })} />
        <AccentPicker value={p.accent_color} onChange={(accent_color) => set({ accent_color })} />
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Field label="Creator name">
            {(c) => <Input {...c} value={p.creator_name} maxLength={60} onChange={(e) => set({ creator_name: e.target.value })} />}
          </Field>
          <Field label="Creator headline">
            {(c) => (
              <Input
                {...c}
                value={p.creator_headline}
                maxLength={80}
                onChange={(e) => set({ creator_headline: e.target.value })}
              />
            )}
          </Field>
        </div>
        <Switch
          checked={p.listed}
          onCheckedChange={(listed) => set({ listed })}
          label="List in the marketplace"
          description="Unlisted agents stay reachable by link and over A2A."
        />
      </CardBody>
      <SaveBar
        dirty={Object.keys(patch).length > 0 && !avatarBusy}
        saving={save.isPending}
        error={save.error}
        onReset={() => {
          setP(initial);
          setShowErrors(false);
        }}
        onSave={() => {
          if (Object.keys(errors).length > 0) {
            setShowErrors(true);
            return;
          }
          save.mutate(patch);
        }}
      />
    </Card>
  );
}

function RuntimeSection({ agent, onUpdateVersion }: { agent: Agent; onUpdateVersion: () => void }) {
  const meta = useMeta();
  const [initial, setInitial] = useState(() => runtimeFromAgent(agent));
  const [r, setR] = useState(initial);
  const [showErrors, setShowErrors] = useState(false);
  const save = useSave(agent, (a) => {
    const next = runtimeFromAgent(a);
    setInitial(next);
    setR(next);
    setShowErrors(false);
    toast.success("Saved. Update the version to make it live.", {
      action: { label: "Update version", onClick: onUpdateVersion },
    });
  });
  const patch = runtimePatch(initial, r);
  const errors = validateRuntime(r);
  const shown = showErrors ? errors : {};
  const set = (patch: Partial<typeof r>) => setR((cur) => ({ ...cur, ...patch }));
  const models = meta.data?.chat_models ?? [];
  const modelOptions = (models.includes(r.model) || !r.model ? models : [r.model, ...models]).map((m) => ({
    value: m,
    label: m === meta.data?.default_chat_model ? `${m} (default)` : m,
  }));
  const locked = initial.record_fields.map((f) => f.key);

  return (
    <Card>
      <CardHeader
        title="Runtime"
        description="Persona, model, web search and lead capture. Changes apply after you update the version."
        actions={
          agent.config_dirty ? (
            <Button size="sm" icon={<Rocket className="h-4 w-4" />} onClick={onUpdateVersion}>
              Update version
            </Button>
          ) : null
        }
      />
      <CardBody className="space-y-5">
        {agent.config_dirty ? (
          <div className="flex items-start gap-2 rounded-control bg-warning-soft p-3 text-sm text-warning">
            <AlertTriangle aria-hidden className="mt-0.5 h-4 w-4 shrink-0" />
            Saved changes are waiting for a new version. Visitors still talk to version {agent.active_version}.
          </div>
        ) : null}
        <Field label="Persona" error={shown.soul}>
          {(c) => (
            <Textarea
              {...c}
              autoResize
              rows={6}
              maxRows={16}
              value={r.soul}
              maxLength={LIMITS.soul}
              onChange={(e) => set({ soul: e.target.value })}
            />
          )}
        </Field>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Field label="Model" error={shown.model}>
            {(c) => (
              <Select
                id={c.id}
                value={r.model}
                options={modelOptions}
                onChange={(e) => {
                  const model = e.target.value;
                  set({ model, temperature: model.startsWith("gemini-2.5") ? r.temperature : null });
                }}
              />
            )}
          </Field>
          <Field label="Temperature" hint="Gemini 3 works best with the model default.">
            {(c) => (
              <Select
                id={c.id}
                value={r.temperature === null ? "" : String(r.temperature)}
                options={temperatureOptions(r.model, r.temperature)}
                onChange={(e) => set({ temperature: e.target.value === "" ? null : Number(e.target.value) })}
              />
            )}
          </Field>
        </div>
        <Switch
          checked={r.web_search_enabled}
          onCheckedChange={(web_search_enabled) => set({ web_search_enabled })}
          label="Ground answers with Google Search"
        />
        <div className="space-y-3 rounded-card border border-border p-4">
          <Switch
            checked={r.records_enabled}
            onCheckedChange={(records_enabled) => set({ records_enabled })}
            label="Lead capture"
            description="The agent fills these fields while it chats. Field types with saved data cannot change."
          />
          {r.records_enabled ? (
            <RecordFieldsEditor
              value={r.record_fields}
              lockedKeys={locked}
              onChange={(record_fields) => set({ record_fields })}
            />
          ) : null}
          {shown.records ? <p className="text-[13px] text-danger">{shown.records}</p> : null}
        </div>
        <div className="text-[13px] text-muted">
          Skills: {agent.skills.map((s) => s.name).join(", ") || "—"}
        </div>
      </CardBody>
      <SaveBar
        dirty={Object.keys(patch).length > 0}
        saving={save.isPending}
        error={save.error}
        note={
          hasCode(save.error, ErrorCodes.record_field_type_locked)
            ? undefined
            : "Saving does not change the live agent until you update the version."
        }
        onReset={() => {
          setR(initial);
          setShowErrors(false);
        }}
        onSave={() => {
          if (Object.keys(errors).length > 0) {
            setShowErrors(true);
            return;
          }
          save.mutate(patch);
        }}
      />
    </Card>
  );
}

function PricingSection({ agent }: { agent: Agent }) {
  const [initial, setInitial] = useState(() => pricingFormFromPricing(agent.pricing));
  const [form, setForm] = useState(initial);
  const [showErrors, setShowErrors] = useState(false);
  const save = useSave(agent, (a) => {
    const next = pricingFormFromPricing(a.pricing);
    setInitial(next);
    setForm(next);
    setShowErrors(false);
    toast.success("Pricing saved. It applies from the next round.");
  });
  const parsed = parsePricing(form);
  const dirty = JSON.stringify(parsePricing(initial).pricing) !== JSON.stringify(parsed.pricing) || !parsed.pricing;

  return (
    <Card>
      <CardHeader title="Pricing" description="Applies from the next round or call." />
      <CardBody>
        <PricingFields value={form} errors={showErrors ? parsed.errors : {}} onChange={setForm} />
      </CardBody>
      <SaveBar
        dirty={dirty}
        saving={save.isPending}
        error={save.error}
        onReset={() => {
          setForm(initial);
          setShowErrors(false);
        }}
        onSave={() => {
          if (!parsed.pricing) {
            setShowErrors(true);
            return;
          }
          save.mutate({ pricing: parsed.pricing });
        }}
      />
    </Card>
  );
}

function StatusSection({ agent }: { agent: Agent }) {
  const navigate = useNavigate();
  const update = useAgentCacheUpdater();
  const [confirmDelete, setConfirmDelete] = useState(false);
  const toggle = useMutation({
    mutationFn: () => (agent.status === "active" ? agentApi.pause(agent.id) : agentApi.resume(agent.id)),
    onSuccess: (a) => {
      update(a);
      toast.success(a.status === "active" ? "Agent resumed" : "Agent paused");
    },
  });
  const remove = useAgentDelete(agent, () => navigate("/studio", { replace: true }));

  return (
    <Card className="border-danger/30">
      <CardHeader title="Status" />
      <CardBody className="space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="min-w-0">
            <div className="text-sm font-medium">{agent.status === "active" ? "Agent is live" : "Agent is paused"}</div>
            <p className="text-[13px] text-muted">
              {agent.status === "active"
                ? "Pausing hides it from the marketplace and stops new chats and A2A calls."
                : "Resume to accept chats and A2A calls again."}
            </p>
          </div>
          <Button
            variant="outline"
            icon={agent.status === "active" ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4" />}
            loading={toggle.isPending}
            onClick={() => toggle.mutate()}
          >
            {agent.status === "active" ? "Pause" : "Resume"}
          </Button>
        </div>
        {toggle.error ? <ErrorLine error={toggle.error} /> : null}
        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border pt-4">
          <div className="min-w-0">
            <div className="text-sm font-medium text-danger">Delete agent</div>
            <p className="text-[13px] text-muted">The public page goes offline. Its address stays reserved.</p>
          </div>
          <Button variant="danger" icon={<Trash2 className="h-4 w-4" />} onClick={() => setConfirmDelete(true)}>
            Delete
          </Button>
        </div>
      </CardBody>
      <Dialog
        open={confirmDelete}
        onOpenChange={(o) => !remove.isPending && setConfirmDelete(o)}
        title={`Delete ${agent.title}?`}
        description="Visitors and AI agents can no longer use it. Earnings and records you already have are kept."
        size="sm"
        footer={
          <>
            <Button variant="outline" disabled={remove.isPending} onClick={() => setConfirmDelete(false)}>
              Cancel
            </Button>
            <Button variant="danger" loading={remove.isPending} onClick={() => remove.mutate()}>
              Delete agent
            </Button>
          </>
        }
      >
        {remove.error ? <ErrorLine error={remove.error} /> : null}
      </Dialog>
    </Card>
  );
}

function useAgentDelete(agent: Agent, onDone: () => void) {
  const update = useAgentCacheUpdater();
  return useMutation({
    mutationFn: () => agentApi.remove(agent.id),
    onSuccess: () => {
      update({ ...agent, status: "paused", listed: false });
      toast.success(`${agent.title} deleted`);
      onDone();
    },
  });
}

export function SettingsTab({ agent, onUpdateVersion }: { agent: Agent; onUpdateVersion: () => void }) {
  return (
    <div className="space-y-6">
      <ProfileSection agent={agent} />
      <RuntimeSection agent={agent} onUpdateVersion={onUpdateVersion} />
      <PricingSection agent={agent} />
      <StatusSection agent={agent} />
    </div>
  );
}
