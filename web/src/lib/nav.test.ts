import { describe, expect, it } from "vitest";
import { loginPath, safeNextPath } from "./nav";

describe("safeNextPath", () => {
  it("accepts same-origin paths", () => {
    expect(safeNextPath("/studio")).toBe("/studio");
    expect(safeNextPath("/a/visa-advisor?x=1#top")).toBe("/a/visa-advisor?x=1#top");
  });

  it("rejects open redirects", () => {
    for (const bad of ["//evil.com", "https://evil.com", "javascript:alert(1)", "/\\evil.com", "studio", "/a\nb"]) {
      expect(safeNextPath(bad)).toBe("/");
    }
    expect(safeNextPath(null, "/wallet")).toBe("/wallet");
  });

  it("builds the login path with an encoded next", () => {
    expect(loginPath("/wallet")).toBe("/login?next=%2Fwallet");
    expect(loginPath("/studio/agents/agt_1", "?tab=records")).toBe("/login?next=%2Fstudio%2Fagents%2Fagt_1%3Ftab%3Drecords");
    expect(loginPath("/")).toBe("/login");
  });
});
