import { describe, expect, it } from "vitest";

import { ivPremiumSentence, ivPremiumTone } from "./eventMarks";

const iv = (atm: number | null, rv: number | null, ratio: number | null) => ({
  atm_iv: atm,
  rank: null,
  samples: 3,
  realized_vol_20d: rv,
  iv_over_realized: ratio,
});

describe("IV against realised vol", () => {
  it("uses the Screener's bands, with 1.0 counted as fair rather than cheap", () => {
    expect(ivPremiumTone(1.2)).toBe("rich");
    expect(ivPremiumTone(1.0)).toBe("mid");
    expect(ivPremiumTone(0.95)).toBe("cheap");
    expect(ivPremiumTone(null)).toBeNull();
  });

  it("says both numbers behind the ratio, and nothing without them", () => {
    expect(ivPremiumSentence(iv(0.42, 0.31, 1.35))).toBe("IV 1.35× realised · premium rich (42 % vs 31 % over 20 sessions)");
    expect(ivPremiumSentence(iv(0.42, null, null))).toBeNull();
  });
});
