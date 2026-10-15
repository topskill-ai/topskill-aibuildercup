import { describe, expect, it } from "vitest";
import type { RecordField } from "@/api/types";
import { batchFollowAll, FOLLOW_BATCH_MAX } from "./api";
import {
  formatRecordValue,
  keyFromLabel,
  maskedKeys,
  newRecordField,
  optionsFromLines,
  personalDataLabels,
  PII_MASK,
  tableColumns,
  validateRecordFields,
} from "./schema";

const field = (over: Partial<RecordField>): RecordField => ({
  key: "k",
  label: "K",
  type: "text",
  description: "",
  options: null,
  required: false,
  pii: false,
  show_in_table: false,
  min: null,
  max: null,
  ...over,
});

describe("record schema helpers", () => {
  it("derives valid, unique keys from labels", () => {
    expect(keyFromLabel("Visa type (main)")).toBe("visa_type_main");
    expect(keyFromLabel("2026 budget")).toBe("budget");
    expect(keyFromLabel("!!!")).toBe("field");
    expect(keyFromLabel("Budget", ["budget"])).toBe("budget_2");
    expect(newRecordField([]).show_in_table).toBe(true);
  });

  it("validates labels, keys, options and ranges", () => {
    expect(validateRecordFields([field({})])).toEqual([]);
    expect(validateRecordFields([field({ label: "" })])[0]).toMatch(/label/);
    expect(validateRecordFields([field({ key: "Bad" })])[0]).toMatch(/key/);
    expect(validateRecordFields([field({}), field({ label: "B" })])[0]).toMatch(/twice/);
    expect(validateRecordFields([field({ type: "single_choice", options: [] })])[0]).toMatch(/option/);
    expect(validateRecordFields([field({ type: "number", min: 5, max: 1 })])[0]).toMatch(/minimum/);
  });

  it("table columns: show_in_table, else the first three", () => {
    const fields = ["a", "b", "c", "d"].map((k) => field({ key: k }));
    expect(tableColumns({ version: 1, scope: "session", fields }).map((f) => f.key)).toEqual(["a", "b", "c"]);
    fields[3].show_in_table = true;
    expect(tableColumns({ version: 1, scope: "session", fields }).map((f) => f.key)).toEqual(["d"]);
    expect(tableColumns(null)).toEqual([]);
  });

  it("formats values with option labels", () => {
    const f = field({ type: "multi_choice", options: [{ key: "br", label: "Brazil" }] });
    const v = (value: unknown) => ({ value: value as never, evidence: null, updated_at: "", source: "tool" as const });
    expect(formatRecordValue(f, v(["br", "xx"]))).toBe("Brazil, xx");
    expect(formatRecordValue(field({ type: "boolean" }), v(true))).toBe("Yes");
    expect(formatRecordValue(field({}), v(null))).toBe("—");
    expect(formatRecordValue(field({}), undefined)).toBe("—");
  });
});

describe("optionsFromLines", () => {
  const prev = [
    { key: "germany", label: "Germany" },
    { key: "france", label: "France" },
  ];

  it("keeps the key when a saved field's option is relabelled", () => {
    const out = optionsFromLines("Germany (DE)\nFrance", prev, true);
    expect(out).toEqual([
      { key: "germany", label: "Germany (DE)" },
      { key: "france", label: "France" },
    ]);
  });

  it("keeps keys of unchanged labels even when lines are reordered", () => {
    const out = optionsFromLines("France\nGermany", prev, true);
    expect(out.map((o) => o.key)).toEqual(["france", "germany"]);
  });

  it("gives new lines fresh keys that never collide with saved ones", () => {
    const out = optionsFromLines("Germany\nFrance\nGermany ", prev, true);
    expect(out.map((o) => o.key)).toEqual(["germany", "france", "germany_2"]);
    const added = optionsFromLines("Germany\nFrance\nSpain", prev, true);
    expect(added[2]).toEqual({ key: "spain", label: "Spain" });
  });

  it("derives keys from labels for unsaved fields", () => {
    const out = optionsFromLines("Germany (DE)\nFrance", prev, false);
    expect(out.map((o) => o.key)).toEqual(["germany_de", "france"]);
  });
});

describe("personal data from stored values", () => {
  const value = (v: string) => ({ value: v, evidence: null, updated_at: "", source: "tool" as const });
  const schema = {
    version: 1 as const,
    scope: "session" as const,
    fields: [
      { ...newRecordField([]), key: "email", label: "Email", pii: true },
      { ...newRecordField([]), key: "country", label: "Country", pii: false },
    ],
  };

  it("lists masked keys, including retired ones", () => {
    expect(maskedKeys({ data: { email: value(PII_MASK), phone: value(PII_MASK), country: value("DE") } })).toEqual([
      "email",
      "phone",
    ]);
  });

  it("adds retired or un-flagged masked keys to the schema's PII labels", () => {
    const rows = [{ data: { email: value(PII_MASK), phone: value(PII_MASK), country: value(PII_MASK) } }];
    expect(personalDataLabels(schema, rows)).toEqual(["Email", "phone (retired)", "Country"]);
    expect(personalDataLabels(null, [])).toEqual([]);
  });
});

describe("batchFollowAll", () => {
  it("posts at most 200 ids per call and sums the updates", async () => {
    const ids = Array.from({ length: 450 }, (_, i) => `r${i}`);
    const calls: number[] = [];
    const out = await batchFollowAll("a1", ids, "done", async (_a, chunk) => {
      calls.push(chunk.length);
      return { updated: chunk.length };
    });
    expect(calls).toEqual([FOLLOW_BATCH_MAX, FOLLOW_BATCH_MAX, 50]);
    expect(out.updated).toBe(450);
  });
});
