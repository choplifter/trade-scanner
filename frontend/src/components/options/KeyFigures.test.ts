import { describe, expect, it } from "vitest";

import type { OptionEventsResponse, ResolvedSpread } from "../../types/options";
import { heldFigures, keyFigures } from "./KeyFigures";

const spread = (over: Partial<ResolvedSpread> = {}) =>
  ({ expiry: "2026-11-20", dte: 40, net_mid: 1.0, net_natural: 0.85, max_loss: -800, expected_value: 12, direction: "credit", ...over }) as ResolvedSpread;

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

describe("P/L per day and Vol", () => {
  it("spreads EV (RV) over the days left and shows IV against RV as levels", () => {
    const figs = keyFigures(
      spread({ expected_value_rv: 28, dte: 40, atm_iv: 0.153, vol_forecast: { forecast: 0.105, recent: 0.1, long_run: 0.12, weight_recent: 0.5 } }),
      null,
      100_000,
      true,
    );
    const byLabel = Object.fromEntries(figs.map((f) => [f.label, f]));
    expect(byLabel["P/L / day"].value).toBe("+$0.70");
    expect(byLabel["P/L / day"].tone).toBe("good");
    expect(byLabel["Vol"].value).toBe("IV 15.3 % · RV 10.5 %");
    expect(byLabel["Vol"].tone).toBe("good"); // 1.46x: rich, good for a seller
  });
});

describe("a held spread's forward figures", () => {
  const outlook = {
    expected_value_rv: -1518, pnl_per_day: -34.5, dte: 44, iv: 0.55, rv_forecast: 0.63,
    chance_gain: 0.83, chance_loss: 0.17, avg_gain: 1200, avg_loss: -14800, max_gain: 1978, max_loss: -27022,
    vol_forecast: { forecast: 0.63, recent: 0.47, long_run: 0.81, weight_recent: 0.6 },
  };
  const n = (v: number) => v.toLocaleString(undefined, { maximumFractionDigits: 0 });
  it("shows holding to expiry as two chances and amounts, the averages in the tooltip", () => {
    const [held, vol] = heldFigures(outlook, 58, true);
    expect(held.label).toBe("If held");
    expect(held.value).toBe("83% +$2.0k · 17% −$27k");
    expect(held.tone).toBe("bad");
    expect(held.title).toContain("44 days");
    expect(held.title).toContain(`adding up to $${n(1978)} (on average $${n(1200)})`);
    expect(held.title).toContain(`losing up to $${n(27022)} (on average $${n(14800)})`);
    expect(held.title).toContain("+$58.00");
    expect(vol.value).toBe("IV 55.0 % · RV 63.0 %");
    expect(vol.tone).toBe("bad"); // 0.87x: cheap, bad for a seller
  });
  it("says unlimited for a side with no largest value", () => {
    const [held] = heldFigures({ ...outlook, max_loss: null }, null, true);
    expect(held.value).toBe("83% +$2.0k · 17% −∞");
    expect(held.title).toContain("losing up to unlimited");
  });
});
