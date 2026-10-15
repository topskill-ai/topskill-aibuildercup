import { describe, expect, it } from "vitest";
import { a2aPriceLabel, priceLabel } from "./labels";

const p = { mode: "per_round" as const, price_cents: 100, free_rounds: 2, a2a_enabled: true, a2a_price_cents: 200 };

describe("price labels", () => {
  it("formats round and A2A prices", () => {
    expect(priceLabel(p)).toBe("1.00 / round · 2 free");
    expect(priceLabel({ ...p, free_rounds: 0 })).toBe("1.00 / round");
    expect(priceLabel({ ...p, mode: "free" })).toBe("Free");
    expect(a2aPriceLabel(p)).toBe("A2A 2.00 / call");
    expect(a2aPriceLabel({ ...p, a2a_enabled: false })).toBe("A2A off");
  });
});
