/**
 * Pricing form (wizard Step 3 and the Settings tab). The live line is computed in integer cents
 * with the backend's floor from `meta.revenue_share_bp` (SPEC §11.4) — never a hardcoded ratio.
 */
import { Coins } from "lucide-react";
import { useMeta } from "@/api/meta";
import { Field, Input } from "@/components/ui/Input";
import { Skeleton } from "@/components/ui/Skeleton";
import { Switch } from "@/components/ui/Switch";
import { cn } from "@/lib/cn";
import { a2aLine, effectiveA2aPrice, parsePricing, pricingLine, type FieldErrors, type PricingForm } from "./wizard";

export function PricingFields({
  value,
  onChange,
  errors = {},
  disabled,
}: {
  value: PricingForm;
  onChange: (next: PricingForm) => void;
  errors?: FieldErrors;
  disabled?: boolean;
}) {
  const meta = useMeta();
  const set = (patch: Partial<PricingForm>) => onChange({ ...value, ...patch });
  const parsed = parsePricing(value).pricing;
  const bp = meta.data?.revenue_share_bp;

  return (
    <div className="space-y-6">
      <div role="radiogroup" aria-label="Pricing mode" className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {(
          [
            { mode: "per_round", title: "Paid per round", text: "Visitors pay for each answer after their free rounds." },
            { mode: "free", title: "Free", text: "Anyone can chat for free. Useful for lead capture." },
          ] as const
        ).map((o) => (
          <button
            key={o.mode}
            type="button"
            role="radio"
            aria-checked={value.mode === o.mode}
            disabled={disabled}
            onClick={() => set({ mode: o.mode })}
            className={cn(
              "rounded-card border p-4 text-left transition-colors",
              value.mode === o.mode ? "border-primary bg-primary-soft" : "border-border bg-surface hover:border-border-strong",
            )}
          >
            <div className="text-sm font-semibold">{o.title}</div>
            <div className="mt-0.5 text-[13px] text-muted">{o.text}</div>
          </button>
        ))}
      </div>

      {value.mode === "per_round" ? (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Field label="Price per round (credits)" error={errors.price}>
            {(c) => (
              <Input
                {...c}
                mono
                inputMode="decimal"
                value={value.price}
                disabled={disabled}
                onChange={(e) => set({ price: e.target.value })}
              />
            )}
          </Field>
          <Field label="Free rounds per visitor" error={errors.free_rounds}>
            {(c) => (
              <Input
                {...c}
                mono
                inputMode="numeric"
                value={value.free_rounds}
                disabled={disabled}
                onChange={(e) => set({ free_rounds: e.target.value })}
              />
            )}
          </Field>
        </div>
      ) : null}

      <div className="rounded-card border border-border p-4">
        <Switch
          checked={value.a2a_enabled}
          disabled={disabled}
          onCheckedChange={(on) => set({ a2a_enabled: on })}
          label="Callable by AI agents (A2A)"
          description="Other agents pay per call with a signed budget mandate. No free rounds."
        />
        {value.a2a_enabled ? (
          <Field
            className="mt-4 sm:max-w-xs"
            label="Price per A2A call (credits)"
            hint={value.a2a_price_touched ? undefined : "Follows the round price until you change it."}
            error={errors.a2a_price}
          >
            {(c) => (
              <Input
                {...c}
                mono
                inputMode="decimal"
                value={effectiveA2aPrice(value)}
                disabled={disabled}
                onChange={(e) => set({ a2a_price: e.target.value, a2a_price_touched: true })}
              />
            )}
          </Field>
        ) : null}
      </div>

      <div
        className="flex items-start gap-3 rounded-card border border-primary/20 bg-primary-soft p-4 text-sm"
        data-testid="pricing-line"
        aria-live="polite"
      >
        <Coins aria-hidden className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
        <div className="min-w-0 space-y-1">
          {bp === undefined ? (
            <Skeleton className="h-4 w-72 max-w-full" />
          ) : parsed ? (
            <>
              <p className="font-medium text-text">{pricingLine(parsed, bp)}</p>
              <p className="text-muted">{a2aLine(parsed, bp)}</p>
            </>
          ) : (
            <p className="text-muted">Fix the highlighted fields to see what visitors pay.</p>
          )}
          {value.rationale ? <p className="text-[13px] text-muted">Gemini suggested: {value.rationale}</p> : null}
        </div>
      </div>
    </div>
  );
}
