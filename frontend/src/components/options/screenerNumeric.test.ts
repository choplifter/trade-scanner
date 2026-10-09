import { describe, expect, it } from "vitest";

import { numeric } from "./ScreenerTab";

describe("the screener's number fields", () => {
  it("takes 0 as today's expiry where allowed, and a blank as the fallback", () => {
    expect(numeric("0", 30, true)).toBe(0);
    expect(numeric("0", 30)).toBe(30);
    expect(numeric("", 30, true)).toBe(30);
    expect(numeric("45", 30)).toBe(45);
  });
});
