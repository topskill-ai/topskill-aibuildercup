/**
 * Small controlled editors shared by the publish wizard and the agent Settings tab:
 * starter questions, capabilities and the accent picker.
 */
import { Check, Plus, Trash2 } from "lucide-react";
import type { AccentColor, Capability, StarterQuestion } from "@/api/types";
import { Button } from "@/components/ui/Button";
import { Input, Label } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import { ACCENT_COLORS, accentClasses } from "@/lib/accent";
import { cn } from "@/lib/cn";
import { CapabilityIcon } from "@/features/studio/CapabilityIcon";
import { CAPABILITY_ICON_NAMES, capabilityIconLabel } from "@/features/studio/capabilityIcons";
import { LIMITS } from "./wizard";

export function StarterQuestionsEditor({
  value,
  onChange,
  errors = {},
  disabled,
}: {
  value: StarterQuestion[];
  onChange: (next: StarterQuestion[]) => void;
  errors?: Record<string, string>;
  disabled?: boolean;
}) {
  const set = (i: number, patch: Partial<StarterQuestion>) =>
    onChange(value.map((q, j) => (j === i ? { ...q, ...patch } : q)));
  return (
    <div className="min-w-0">
      <Label>Starter questions</Label>
      <p className="-mt-1 mb-2 text-[13px] text-muted">Shown as buttons on the agent page. Up to 3.</p>
      <div className="space-y-3">
        {value.map((q, i) => (
          <div key={i} className="rounded-control border border-border p-3">
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-[minmax(0,1fr)_minmax(0,2fr)_auto]">
              <Input
                aria-label={`Question ${i + 1} label`}
                placeholder="Short label"
                value={q.label}
                maxLength={LIMITS.starterLabel}
                disabled={disabled}
                onChange={(e) => set(i, { label: e.target.value })}
              />
              <Input
                aria-label={`Question ${i + 1} text`}
                placeholder="The question the visitor sends"
                value={q.prompt}
                maxLength={LIMITS.starterPrompt}
                disabled={disabled}
                onChange={(e) => set(i, { prompt: e.target.value })}
              />
              <Button
                variant="ghost"
                size="icon"
                aria-label={`Remove question ${i + 1}`}
                disabled={disabled}
                onClick={() => onChange(value.filter((_, j) => j !== i))}
              >
                <Trash2 className="h-4 w-4" />
              </Button>
            </div>
            {errors[`starter_questions.${i}`] ? (
              <p className="mt-1.5 text-[13px] text-danger">{errors[`starter_questions.${i}`]}</p>
            ) : null}
          </div>
        ))}
      </div>
      {value.length < LIMITS.starterQuestions ? (
        <Button
          variant="outline"
          size="sm"
          className="mt-2"
          icon={<Plus className="h-4 w-4" />}
          disabled={disabled}
          onClick={() => onChange([...value, { label: "", prompt: "" }])}
        >
          Add question
        </Button>
      ) : null}
    </div>
  );
}

export function CapabilitiesEditor({
  value,
  onChange,
  errors = {},
  disabled,
}: {
  value: Capability[];
  onChange: (next: Capability[]) => void;
  errors?: Record<string, string>;
  disabled?: boolean;
}) {
  const set = (i: number, patch: Partial<Capability>) =>
    onChange(value.map((c, j) => (j === i ? { ...c, ...patch } : c)));
  return (
    <div className="min-w-0">
      <Label>Capabilities</Label>
      <p className="-mt-1 mb-2 text-[13px] text-muted">What the agent is good at. Up to 6.</p>
      <div className="space-y-3">
        {value.map((c, i) => (
          <div key={i} className="rounded-control border border-border p-3">
            <div className="grid grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-2">
              <span className="flex h-10 w-10 items-center justify-center rounded-control bg-primary-soft text-primary">
                <CapabilityIcon name={c.icon} className="h-4 w-4" />
              </span>
              <Input
                aria-label={`Capability ${i + 1} name`}
                placeholder="Name"
                value={c.name}
                maxLength={LIMITS.capabilityName}
                disabled={disabled}
                onChange={(e) => set(i, { name: e.target.value })}
              />
              <Button
                variant="ghost"
                size="icon"
                aria-label={`Remove capability ${i + 1}`}
                disabled={disabled}
                onClick={() => onChange(value.filter((_, j) => j !== i))}
              >
                <Trash2 className="h-4 w-4" />
              </Button>
            </div>
            <div className="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-[180px_minmax(0,1fr)]">
              <Select
                aria-label={`Capability ${i + 1} icon`}
                value={c.icon}
                disabled={disabled}
                options={CAPABILITY_ICON_NAMES.map((n) => ({ value: n, label: capabilityIconLabel(n) }))}
                onChange={(e) => set(i, { icon: e.target.value })}
              />
              <Input
                aria-label={`Capability ${i + 1} description`}
                placeholder="One-line description"
                value={c.description}
                maxLength={LIMITS.capabilityDescription}
                disabled={disabled}
                onChange={(e) => set(i, { description: e.target.value })}
              />
            </div>
            {errors[`capabilities.${i}`] ? (
              <p className="mt-1.5 text-[13px] text-danger">{errors[`capabilities.${i}`]}</p>
            ) : null}
          </div>
        ))}
      </div>
      {value.length < LIMITS.capabilities ? (
        <Button
          variant="outline"
          size="sm"
          className="mt-2"
          icon={<Plus className="h-4 w-4" />}
          disabled={disabled}
          onClick={() => onChange([...value, { icon: "sparkles", name: "", description: "" }])}
        >
          Add capability
        </Button>
      ) : null}
    </div>
  );
}

export function AccentPicker({
  value,
  onChange,
  disabled,
}: {
  value: AccentColor;
  onChange: (next: AccentColor) => void;
  disabled?: boolean;
}) {
  return (
    <div>
      <Label>Accent color</Label>
      <div role="radiogroup" aria-label="Accent color" className="flex flex-wrap gap-2">
        {ACCENT_COLORS.map((c) => {
          const selected = c === value;
          return (
            <button
              key={c}
              type="button"
              role="radio"
              aria-checked={selected}
              aria-label={c}
              disabled={disabled}
              onClick={() => onChange(c)}
              className={cn(
                "flex h-9 w-9 items-center justify-center rounded-full text-white ring-offset-2 transition-shadow",
                accentClasses(c).solid,
                selected ? cn("ring-2", accentClasses(c).ring) : "hover:ring-2 hover:ring-border-strong",
              )}
            >
              {selected ? <Check aria-hidden className="h-4 w-4" /> : null}
            </button>
          );
        })}
      </div>
    </div>
  );
}
