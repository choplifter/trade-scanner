import { describe, expect, it } from "vitest";

import type { LegQuote } from "../../types/options";
import { roundStep, spreadFraction, spreadGrade } from "./ChainTable";

function quote(bid: number | null, ask: number | null): LegQuote {
  return {
    symbol: "X", strike: 100, kind: "call", expiry: "2026-11-20", bid, ask, mid: null, last: null,
    bid_size: null, ask_size: null, delta: null, gamma: null, theta: null, iv: null, open_interest: 0, tradable: true,
  };
}

describe("the readable chain", () => {
  it("grades a quote's width against its mid the way the Screener does", () => {
    expect(spreadGrade(spreadFraction(quote(1.0, 1.08)))).toBe("tight");
    expect(spreadGrade(spreadFraction(quote(1.0, 1.2)))).toBe("fair");
    expect(spreadGrade(spreadFraction(quote(0.2, 1.3)))).toBe("wide");
    expect(spreadGrade(spreadFraction(quote(0, 0.4)))).toBe("none");
    expect(spreadGrade(spreadFraction(null))).toBe("none");
  });

  it("rules the strikes at about five steps, rounded to a counting number", () => {
    expect(roundStep([95, 96, 97, 98, 99, 100])).toBe(5);
    expect(roundStep([90, 92.5, 95, 97.5, 100])).toBe(10);
    expect(roundStep([700, 705, 710, 715])).toBe(25);
    expect(roundStep([100])).toBeNull();
  });
});
