import { describe, expect, it } from "vitest";

import { orderSummary } from "./SpreadTicket";

const base = { market: false, tif: "day" as const, limit: 0.36, mid: 0.36, natural: 0.3, direction: "credit" as const };

describe("the ticket's order line", () => {
  it("says the type, the price and where it sits, and the time in force", () => {
    expect(orderSummary(base)).toBe("Limit 0.36 credit (= mid) · Day — cancelled at the close if unfilled");
    expect(orderSummary({ ...base, limit: 0.3 })).toContain("(= natural)");
    expect(orderSummary({ ...base, limit: 0.34 })).toContain("(your price)");
    // A mid of 0.345 is prefilled as its toFixed(2) and still reads as the mid.
    expect(orderSummary({ ...base, mid: 0.345, limit: Number((0.345).toFixed(2)) })).toContain("(= mid)");
    expect(orderSummary({ ...base, tif: "gtc" })).toContain("GTC — rests until filled or cancelled");
  });

  it("has no price before one is typed, and a market order is a day order", () => {
    expect(orderSummary({ ...base, limit: null })).toBe("Limit 0.36 credit (at the mid) · Day — cancelled at the close if unfilled");
    expect(orderSummary({ ...base, market: true, tif: "gtc" })).toBe("Market ≈ 0.30 credit · Day (market orders are day only)");
  });
});
