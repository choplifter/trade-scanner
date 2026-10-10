import { describe, expect, it } from "vitest";

import type { ClosedSpread } from "../../types/options";
import { exitLabel, netLabel, strategyName, strikesLabel } from "./ClosedSpreadsTab";

describe("a closed spread's labels", () => {
  it("reads a credit and a debit the way a trader says them", () => {
    expect(netLabel(-0.09)).toBe("0.09 cr");
    expect(netLabel(1.2)).toBe("1.20 db");
    expect(netLabel(0)).toBe("0.00");
    // Closing a credit spread costs; closing a debit spread pays.
    expect(exitLabel(-2.94)).toBe("2.94 db");
    expect(exitLabel(1.48)).toBe("1.48 cr");
    expect(exitLabel(0)).toBe("0.00");
  });

  it("names the structure and its strikes", () => {
    expect(strategyName("bull_put")).toBe("Bull put");
    expect(strategyName("short_put")).toBe("Short put");
    const spread = {
      legs: [
        { side: "long", strike: 771, kind: "put" },
        { side: "short", strike: 773, kind: "put" },
      ],
    } as unknown as ClosedSpread;
    expect(strikesLabel(spread)).toBe("+771P/−773P");
  });
});
