import { Clock, KeyRound, Lock, ShieldCheck } from "lucide-react";
import { useMemo, useState } from "react";
import { useMeta } from "@/api/meta";
import type { Meta } from "@/api/types";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { ErrorLine } from "@/components/ui/ErrorLine";
import { Field, Input } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import { Textarea } from "@/components/ui/Textarea";
import { formatCredits } from "@/lib/money";
import { useCreateMandate, type CreateMandateResponse } from "@/features/mandates/api";
import { useNow } from "@/features/mandates/model";
import { INTENT_MAX, intentFromTask, ttlOptions, validateBudget, type BudgetForm } from "./budget";

export interface AuthoriseBudgetDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** The task the user is about to send (prefills the intent). */
  task: string;
  /** Called with the signed mandate; the page then sends the task. */
  onAuthorised: (res: CreateMandateResponse) => void;
}

/**
 * "Authorise budget" (SPEC §9.4 UI flow): the human signs off a spending envelope before the
 * assistant may hire any agent. Defaults come from `meta.a2a_mandate_defaults`.
 */
export function AuthoriseBudgetDialog(props: AuthoriseBudgetDialogProps) {
  const meta = useMeta();
  if (!props.open) return null;
  if (!meta.data)
    return (
      <Dialog open onOpenChange={props.onOpenChange} title="Authorise a budget">
        {meta.isError ? <ErrorLine error={meta.error} onRetry={() => void meta.refetch()} /> : <p className="text-sm text-muted">Loading…</p>}
      </Dialog>
    );
  // Remount per opening so the form re-reads the task and the defaults.
  return <BudgetDialogForm {...props} defaults={meta.data.a2a_mandate_defaults} />;
}

function BudgetDialogForm({
  onOpenChange,
  task,
  onAuthorised,
  defaults,
}: AuthoriseBudgetDialogProps & { defaults: Meta["a2a_mandate_defaults"] }) {
  const create = useCreateMandate();
  const now = useNow(15_000);
  const [touched, setTouched] = useState(false);
  const [form, setForm] = useState<BudgetForm>(() => ({
    intent: intentFromTask(task),
    budget: formatCredits(defaults.budget_cents).replace(/,/g, ""),
    maxPerCall: formatCredits(defaults.max_amount_cents).replace(/,/g, ""),
    ttl: String(Math.min(defaults.ttl_minutes, defaults.max_ttl_minutes)),
  }));
  const { errors, request } = useMemo(() => validateBudget(form, defaults), [form, defaults]);
  const options = useMemo(() => ttlOptions(defaults.ttl_minutes, defaults.max_ttl_minutes), [defaults]);
  const set = (key: keyof BudgetForm) => (value: string) => setForm((f) => ({ ...f, [key]: value }));
  const shown = (key: keyof BudgetForm) => (touched ? errors[key] : undefined);

  const submit = () => {
    setTouched(true);
    if (!request || create.isPending) return;
    create.mutate(request, {
      onSuccess: (res) => {
        onAuthorised(res);
        onOpenChange(false);
      },
    });
  };

  const expiresAt = new Date(now + Number(form.ttl || 0) * 60_000);
  const expiryText = Number.isFinite(expiresAt.getTime())
    ? expiresAt.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", hour12: false })
    : "—";

  return (
    <Dialog
      open
      onOpenChange={onOpenChange}
      dismissible={!create.isPending}
      size="md"
      title={
        <span className="flex items-center gap-2">
          <span className="flex h-8 w-8 items-center justify-center rounded-full bg-primary-soft text-primary">
            <ShieldCheck aria-hidden className="h-4 w-4" />
          </span>
          Authorise a budget
        </span>
      }
      description="The assistant can only hire agents within the limits you sign here. Calls above the limits are rejected before anything is sent, and never charged."
      footer={
        <>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={create.isPending}>
            Cancel
          </Button>
          <Button onClick={submit} loading={create.isPending} icon={<Lock aria-hidden className="h-4 w-4" />} data-testid="authorise-submit">
            Authorise and send
          </Button>
        </>
      }
    >
      <form
        className="space-y-4"
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <Field label="Intent" hint="Shown to every agent you pay and signed into the mandate." error={shown("intent")}>
          {(c) => (
            <Textarea
              {...c}
              rows={2}
              maxLength={INTENT_MAX}
              value={form.intent}
              invalid={c.invalid}
              onChange={(e) => set("intent")(e.target.value)}
            />
          )}
        </Field>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Field label="Total budget (credits)" error={shown("budget")}>
            {(c) => (
              <Input {...c} mono inputMode="decimal" value={form.budget} invalid={c.invalid} onChange={(e) => set("budget")(e.target.value)} data-autofocus />
            )}
          </Field>
          <Field label="Max per call (credits)" error={shown("maxPerCall")}>
            {(c) => (
              <Input {...c} mono inputMode="decimal" value={form.maxPerCall} invalid={c.invalid} onChange={(e) => set("maxPerCall")(e.target.value)} />
            )}
          </Field>
        </div>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Field label="Expires in" error={shown("ttl")}>
            {(c) => <Select {...c} options={options} value={form.ttl} onChange={(e) => set("ttl")(e.target.value)} />}
          </Field>
          <Field label="Agents covered">
            {(c) => <Input {...c} value="Any TopSkill agent" readOnly disabled />}
          </Field>
        </div>

        <div className="rounded-card border border-primary/20 bg-primary-soft/60 p-4 text-sm">
          {request ? (
            <p className="text-text">
              The assistant may spend up to{" "}
              <strong className="font-mono tabular-nums">{formatCredits(request.budget_cents)}</strong> credits, at most{" "}
              <strong className="font-mono tabular-nums">{formatCredits(request.max_amount_cents)}</strong> per call, until{" "}
              <strong>{expiryText}</strong>.
            </p>
          ) : (
            <p className="text-muted">Fix the fields above to see the limits you are signing.</p>
          )}
          <ul className="mt-2 space-y-1 text-[13px] text-muted">
            <li className="flex items-center gap-2">
              <KeyRound aria-hidden className="h-3.5 w-3.5 shrink-0 text-primary" />
              Signed by TopSkill (Ed25519) and verified by every agent before it runs.
            </li>
            <li className="flex items-center gap-2">
              <Clock aria-hidden className="h-3.5 w-3.5 shrink-0 text-primary" />
              You can revoke it at any time; failed or rejected calls cost nothing.
            </li>
          </ul>
        </div>
        {create.isError ? <ErrorLine error={create.error} /> : null}
        <button type="submit" hidden aria-hidden tabIndex={-1} />
      </form>
    </Dialog>
  );
}
