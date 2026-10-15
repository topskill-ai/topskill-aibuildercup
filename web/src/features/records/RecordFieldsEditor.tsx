/**
 * Editor for the record schema fields ("Lead capture"), used by the wizard and the Settings tab.
 * Keys are derived from labels for new fields; existing keys stay stable so stored values keep
 * their column (changing a key would retire the old one, SPEC §10.3 CSV).
 */
import { ChevronDown, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import type { FieldType, RecordField } from "@/api/types";
import { Button } from "@/components/ui/Button";
import { Field, Input } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import { Switch } from "@/components/ui/Switch";
import { cn } from "@/lib/cn";
import {
  FIELD_TYPE_LABELS,
  FIELD_TYPES,
  MAX_RECORD_FIELDS,
  isChoiceType,
  keyFromLabel,
  newRecordField,
  optionsFromLines,
  validateRecordFields,
} from "./schema";

function parseNum(s: string): number | null {
  if (s.trim() === "") return null;
  const n = Number(s);
  return Number.isFinite(n) ? n : null;
}

function OptionsEditor({
  field,
  onChange,
  disabled,
  stableKeys,
}: {
  field: RecordField;
  onChange: (patch: Partial<RecordField>) => void;
  disabled?: boolean;
  /** Saved field: relabelled options keep their key so stored values still resolve. */
  stableKeys: boolean;
}) {
  const opts = field.options ?? [];
  const text = opts.map((o) => o.label).join("\n");
  return (
    <Field label="Options" hint="One per line.">
      {(c) => (
        <textarea
          {...c}
          rows={Math.min(6, Math.max(2, opts.length + 1))}
          disabled={disabled}
          defaultValue={text}
          onBlur={(e) => onChange({ options: optionsFromLines(e.target.value, opts, stableKeys) })}
          className="block w-full min-w-0 rounded-control border border-border bg-surface px-3 py-2 text-[15px] leading-6 focus:border-primary focus:outline-none focus:ring-3 focus:ring-primary/15"
        />
      )}
    </Field>
  );
}

export function RecordFieldsEditor({
  value,
  onChange,
  disabled,
  /** Keys that already exist on the server (their key is locked; type changes may 409). */
  lockedKeys = [],
}: {
  value: RecordField[];
  onChange: (next: RecordField[]) => void;
  disabled?: boolean;
  lockedKeys?: string[];
}) {
  const [open, setOpen] = useState<number | null>(null);
  const problems = validateRecordFields(value);

  const set = (i: number, patch: Partial<RecordField>) =>
    onChange(
      value.map((f, j) => {
        if (j !== i) return f;
        const next = { ...f, ...patch };
        // New (unsaved) fields follow their label; saved keys never change.
        if (patch.label !== undefined && !lockedKeys.includes(f.key)) {
          next.key = keyFromLabel(patch.label || "field", value.filter((_, k) => k !== i).map((x) => x.key));
        }
        if (patch.type !== undefined) {
          if (isChoiceType(patch.type) && !next.options) next.options = [];
          if (!isChoiceType(patch.type)) next.options = null;
          if (patch.type !== "number") {
            next.min = null;
            next.max = null;
          }
        }
        return next;
      }),
    );

  return (
    <div className="space-y-3">
      {value.map((f, i) => {
        const expanded = open === i;
        return (
          <div key={i} className="rounded-control border border-border">
            <div className="flex items-center gap-2 p-3">
              <button
                type="button"
                className="flex min-w-0 flex-1 items-center gap-2 text-left"
                aria-expanded={expanded}
                onClick={() => setOpen(expanded ? null : i)}
              >
                <ChevronDown
                  aria-hidden
                  className={cn("h-4 w-4 shrink-0 text-subtle transition-transform", expanded ? "" : "-rotate-90")}
                />
                <span className="truncate text-sm font-medium">{f.label || `Field ${i + 1}`}</span>
                <span className="shrink-0 text-xs text-subtle">{FIELD_TYPE_LABELS[f.type]}</span>
                {f.required ? <span className="shrink-0 text-xs text-warning">required</span> : null}
                {f.pii ? <span className="shrink-0 text-xs text-danger">personal data</span> : null}
              </button>
              <Button
                variant="ghost"
                size="icon-sm"
                aria-label={`Remove ${f.label || `field ${i + 1}`}`}
                disabled={disabled}
                onClick={() => onChange(value.filter((_, j) => j !== i))}
              >
                <Trash2 className="h-4 w-4" />
              </Button>
            </div>
            {expanded ? (
              <div className="space-y-3 border-t border-border p-3">
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                  <Field label="Label" hint={<span className="font-mono">{f.key}</span>}>
                    {(c) => (
                      <Input
                        {...c}
                        value={f.label}
                        maxLength={40}
                        disabled={disabled}
                        onChange={(e) => set(i, { label: e.target.value })}
                      />
                    )}
                  </Field>
                  <Field label="Type">
                    {(c) => (
                      <Select
                        id={c.id}
                        value={f.type}
                        disabled={disabled}
                        options={FIELD_TYPES.map((t) => ({ value: t, label: FIELD_TYPE_LABELS[t] }))}
                        onChange={(e) => set(i, { type: e.target.value as FieldType })}
                      />
                    )}
                  </Field>
                </div>
                <Field label="What to capture" hint="Tells the agent what this field means.">
                  {(c) => (
                    <Input
                      {...c}
                      value={f.description}
                      maxLength={300}
                      disabled={disabled}
                      onChange={(e) => set(i, { description: e.target.value })}
                    />
                  )}
                </Field>
                {isChoiceType(f.type) ? (
                  <OptionsEditor
                    key={`${i}:${f.type}`}
                    field={f}
                    disabled={disabled}
                    stableKeys={lockedKeys.includes(f.key)}
                    onChange={(patch) => set(i, patch)}
                  />
                ) : null}
                {f.type === "number" ? (
                  <div className="grid grid-cols-2 gap-3">
                    <Field label="Minimum">
                      {(c) => (
                        <Input
                          {...c}
                          mono
                          inputMode="decimal"
                          defaultValue={f.min ?? ""}
                          disabled={disabled}
                          onBlur={(e) => set(i, { min: parseNum(e.target.value) })}
                        />
                      )}
                    </Field>
                    <Field label="Maximum">
                      {(c) => (
                        <Input
                          {...c}
                          mono
                          inputMode="decimal"
                          defaultValue={f.max ?? ""}
                          disabled={disabled}
                          onBlur={(e) => set(i, { max: parseNum(e.target.value) })}
                        />
                      )}
                    </Field>
                  </div>
                ) : null}
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
                  <Switch
                    checked={f.required}
                    disabled={disabled}
                    onCheckedChange={(v) => set(i, { required: v })}
                    label="Required"
                  />
                  <Switch
                    checked={f.pii}
                    disabled={disabled}
                    onCheckedChange={(v) => set(i, { pii: v })}
                    label="Personal data"
                  />
                  <Switch
                    checked={f.show_in_table}
                    disabled={disabled}
                    onCheckedChange={(v) => set(i, { show_in_table: v })}
                    label="Show in table"
                  />
                </div>
              </div>
            ) : null}
          </div>
        );
      })}
      {problems.length > 0 ? (
        <ul className="list-disc space-y-0.5 pl-5 text-[13px] text-danger">
          {problems.slice(0, 4).map((p) => (
            <li key={p}>{p}</li>
          ))}
        </ul>
      ) : null}
      {value.length < MAX_RECORD_FIELDS ? (
        <Button
          variant="outline"
          size="sm"
          icon={<Plus className="h-4 w-4" />}
          disabled={disabled}
          onClick={() => {
            onChange([...value, newRecordField(value)]);
            setOpen(value.length);
          }}
        >
          Add field
        </Button>
      ) : null}
    </div>
  );
}
