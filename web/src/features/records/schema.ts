/**
 * Record schema helpers shared by the wizard's "Lead capture" editor, the Settings tab and the
 * Records table (SPEC §5.2 RecordField, §10.3). Mirrors the server's validation so the form can
 * explain problems early; the server stays the source of truth.
 */
import type { FieldType, RecordField, RecordOption, RecordRow, RecordSchema, RecordValue } from "@/api/types";

export const RECORD_KEY_RE = /^[a-z][a-z0-9_]{0,39}$/;
export const MAX_RECORD_FIELDS = 30;
export const MAX_RECORD_OPTIONS = 30;
export const MAX_RECORD_LABEL = 40;
export const MAX_RECORD_DESCRIPTION = 300;

export const FIELD_TYPE_LABELS: Record<FieldType, string> = {
  text: "Short text",
  long_text: "Long text",
  number: "Number",
  boolean: "Yes / no",
  single_choice: "Single choice",
  multi_choice: "Multiple choice",
};

export const FIELD_TYPES = Object.keys(FIELD_TYPE_LABELS) as FieldType[];

export function isChoiceType(t: FieldType): boolean {
  return t === "single_choice" || t === "multi_choice";
}

/** "Visa type (main)" → "visa_type_main"; always matches `RECORD_KEY_RE` (falls back to "field"). */
export function keyFromLabel(label: string, taken: Iterable<string> = []): string {
  let base = label
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .replace(/^[0-9_]+/, "")
    .slice(0, 40)
    .replace(/_+$/g, "");
  if (!base) base = "field";
  const used = new Set(taken);
  if (!used.has(base)) return base;
  for (let i = 2; i < 1000; i += 1) {
    const suffix = `_${i}`;
    const candidate = `${base.slice(0, 40 - suffix.length)}${suffix}`;
    if (!used.has(candidate)) return candidate;
  }
  return base;
}

/**
 * Options from the editor's "one per line" text. A line whose label already exists keeps that
 * option's key. For a saved field (`stableByPosition`), a relabelled line also keeps the key of the
 * option on the same line, so stored values ("germany") still resolve after "Germany" →
 * "Germany (DE)". Only genuinely new lines get a fresh key derived from their label.
 */
export function optionsFromLines(text: string, prev: RecordOption[], stableByPosition: boolean): RecordOption[] {
  const labels = text
    .split("\n")
    .map((l) => l.trim().slice(0, MAX_RECORD_LABEL))
    .filter(Boolean)
    .slice(0, MAX_RECORD_OPTIONS);
  const keys: (string | null)[] = labels.map(() => null);
  const used = new Set<string>();
  labels.forEach((label, i) => {
    const same = prev.find((o) => o.label === label && !used.has(o.key));
    if (same) {
      keys[i] = same.key;
      used.add(same.key);
    }
  });
  if (stableByPosition) {
    labels.forEach((_, i) => {
      const at = prev[i];
      if (keys[i] === null && at && !used.has(at.key) && !labels.includes(at.label)) {
        keys[i] = at.key;
        used.add(at.key);
      }
    });
  }
  const reserved = new Set([...used, ...prev.map((o) => o.key)]);
  return labels.map((label, i) => {
    let key = keys[i];
    if (key === null) {
      key = keyFromLabel(label, reserved);
      reserved.add(key);
    }
    return { key, label };
  });
}

export function newRecordField(existing: RecordField[]): RecordField {
  return {
    key: keyFromLabel("field", existing.map((f) => f.key)),
    label: "",
    type: "text",
    description: "",
    options: null,
    required: false,
    pii: false,
    show_in_table: existing.filter((f) => f.show_in_table).length < 3,
    min: null,
    max: null,
  };
}

/** Fill defaults and drop properties that do not apply to the field's type. */
export function normalizeRecordField(f: Partial<RecordField> & Pick<RecordField, "key" | "label" | "type">): RecordField {
  const choice = isChoiceType(f.type);
  return {
    key: f.key.trim(),
    label: f.label.trim().replace(/\s+/g, " "),
    type: f.type,
    description: (f.description ?? "").trim(),
    options: choice
      ? (f.options ?? []).map((o: RecordOption) => ({ key: o.key.trim(), label: o.label.trim().replace(/\s+/g, " ") }))
      : null,
    required: Boolean(f.required),
    pii: Boolean(f.pii),
    show_in_table: Boolean(f.show_in_table),
    min: f.type === "number" && typeof f.min === "number" && Number.isFinite(f.min) ? f.min : null,
    max: f.type === "number" && typeof f.max === "number" && Number.isFinite(f.max) ? f.max : null,
  };
}

export function normalizeRecordFields(fields: RecordField[]): RecordField[] {
  return fields.map((f) => normalizeRecordField(f));
}

/** Human-readable problems (empty = valid). */
export function validateRecordFields(fields: RecordField[]): string[] {
  const problems: string[] = [];
  if (fields.length > MAX_RECORD_FIELDS) problems.push(`Use at most ${MAX_RECORD_FIELDS} fields.`);
  const seen = new Set<string>();
  fields.forEach((raw, i) => {
    const f = normalizeRecordField(raw);
    const name = f.label || `Field ${i + 1}`;
    if (!f.label) problems.push(`Field ${i + 1} needs a label.`);
    else if (f.label.length > MAX_RECORD_LABEL) problems.push(`"${name}": labels are at most ${MAX_RECORD_LABEL} characters.`);
    if (!RECORD_KEY_RE.test(f.key))
      problems.push(`"${name}": the key must start with a letter and use only a–z, 0–9 and _.`);
    else if (seen.has(f.key)) problems.push(`"${name}": the key "${f.key}" is used twice.`);
    seen.add(f.key);
    if (f.description.length > MAX_RECORD_DESCRIPTION)
      problems.push(`"${name}": the description is at most ${MAX_RECORD_DESCRIPTION} characters.`);
    if (isChoiceType(f.type)) {
      const opts = f.options ?? [];
      if (opts.length === 0) problems.push(`"${name}": add at least one option.`);
      if (opts.length > MAX_RECORD_OPTIONS) problems.push(`"${name}": use at most ${MAX_RECORD_OPTIONS} options.`);
      const optKeys = new Set<string>();
      opts.forEach((o) => {
        if (!o.label || o.label.length > MAX_RECORD_LABEL) problems.push(`"${name}": every option needs a short label.`);
        if (!RECORD_KEY_RE.test(o.key) || optKeys.has(o.key)) problems.push(`"${name}": option keys must be unique.`);
        optKeys.add(o.key);
      });
    }
    if (f.type === "number" && f.min !== null && f.max !== null && f.min > f.max)
      problems.push(`"${name}": the minimum is greater than the maximum.`);
  });
  return Array.from(new Set(problems));
}

/** Table columns: fields marked `show_in_table`, else the first 3 (SPEC §11.4). */
export function tableColumns(schema: RecordSchema | null | undefined): RecordField[] {
  const fields = schema?.fields ?? [];
  const marked = fields.filter((f) => f.show_in_table);
  return marked.length > 0 ? marked : fields.slice(0, 3);
}

/** What the server sends in place of a personal-data value (and its evidence) unless revealed. */
export const PII_MASK = "•••";

/** Keys whose value arrived masked, including keys a later schema retired or un-flagged. */
export function maskedKeys(row: Pick<RecordRow, "data">): string[] {
  return Object.entries(row.data)
    .filter(([, v]) => v?.value === PII_MASK)
    .map(([k]) => k);
}

/**
 * Labels of the personal-data fields a set of rows may hold: the current schema's `pii` fields plus
 * any masked key the schema no longer flags (stored values keep the PII flag they were written with).
 */
export function personalDataLabels(schema: RecordSchema | null | undefined, rows: Pick<RecordRow, "data">[]): string[] {
  const fields = schema?.fields ?? [];
  const labels = fields.filter((f) => f.pii).map((f) => f.label);
  const flagged = new Set(fields.filter((f) => f.pii).map((f) => f.key));
  const extra = new Set<string>();
  for (const r of rows) for (const k of maskedKeys(r)) if (!flagged.has(k)) extra.add(k);
  for (const k of extra) labels.push(fields.find((f) => f.key === k)?.label ?? `${k} (retired)`);
  return labels;
}

/** Display text for a stored record value. */
export function formatRecordValue(field: RecordField | undefined, value: RecordValue | undefined): string {
  const v = value?.value;
  if (v === null || v === undefined || v === "") return "—";
  const label = (key: string) => field?.options?.find((o) => o.key === key)?.label ?? key;
  if (Array.isArray(v)) return v.length ? v.map(label).join(", ") : "—";
  if (typeof v === "boolean") return v ? "Yes" : "No";
  if (typeof v === "number") return Number.isInteger(v) ? v.toLocaleString("en-US") : String(v);
  if (field && field.type === "single_choice") return label(v);
  return v;
}
