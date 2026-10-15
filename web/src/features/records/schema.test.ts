import { describe, expect, it } from "vitest";
import type { RecordField } from "@/api/types";
import { formatRecordValue, keyFromLabel, newRecordField, tableColumns, validateRecordFields } from "./schema";

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
