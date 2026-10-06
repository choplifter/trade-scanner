import { describe, expect, it } from "vitest";

import type { OptionEventsResponse, ResolvedSpread } from "../../types/options";
import { keyFigures } from "./KeyFigures";

const spread = (over: Partial<ResolvedSpread> = {}) =>
  ({ expiry: "2026-11-20", net_mid: 1.0, net_natural: 0.85, max_loss: -800, expected_value: 12, direction: "credit", ...over }) as ResolvedSpread;

const events = (ratio: number | null, report: string | null) =>
  ({ iv: { iv_over_realized: ratio }, earnings: report ? { report_date: report } : null }) as unknown as OptionEventsResponse;

const tones = (figs: ReturnType<typeof keyFigures>) => Object.fromEntries(figs.map((f) => [f.label, f.tone]));

describe("keyFigures", () => {
  it("reads rich premium as good for a seller and bad for a buyer", () => {
    expect(tones(keyFigures(spread(), events(1.4, null), 100_000, true))["IV/RV"]).toBe("good");
    expect(tones(keyFigures(spread({ direction: "debit" }), events(1.4, null), 100_000, false))["IV/RV"]).toBe("bad");
  });

  it("colours EV, max loss against equity, earnings inside the life and the quote", () => {
    const t = tones(keyFigures(spread({ expected_value_rv: -3 }), events(1.0, "2026-11-12"), 100_000, true));
    expect(t).toMatchObject({ EV: "good", "EV (RV)": "bad", "Max loss": "good", Earnings: "bad", Quote: "neutral" });
    const worse = tones(keyFigures(spread({ expected_value: -5, max_loss: -3000, net_natural: 0.6 }), events(1.0, "2026-12-01"), 100_000, true));
    expect(worse).toMatchObject({ EV: "bad", "Max loss": "bad", Earnings: "good", Quote: "bad" });
    expect(tones(keyFigures(spread({ max_loss: null }), null, 100_000, true))["Max loss"]).toBe("bad");
  });
});
