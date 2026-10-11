import { describe, expect, it } from "vitest";

import type { ScreenOutcome } from "../../types/options";
import { passesFilters } from "./ScreenerTab";

const none = { maxLossProb: "", minProfit: "", maxLoss: "", maxLossGain: "", minEv: "", minToBe: "", minMargin: "", minIvRv: "", earningsOk: "" };
const pltr: ScreenOutcome = {
  expected_value: -10.08,
  win_probability: 0.817,
  loss_probability: 0.183,
  max_profit: 76.5,
  max_loss: -423.5,
  risk_reward: 5.54,
  to_breakeven_sigma: -0.98,
};

describe("screen result filters", () => {
  it("lets everything through when no limit is set, even a row without an outcome", () => {
    expect(passesFilters(pltr, none)).toBe(true);
    expect(passesFilters(null, none)).toBe(true);
  });

  it("applies each limit to the structure's own numbers", () => {
    expect(passesFilters(pltr, { ...none, maxLossProb: "20" })).toBe(true);
    expect(passesFilters(pltr, { ...none, maxLossProb: "15" })).toBe(false);
    expect(passesFilters(pltr, { ...none, minProfit: "75" })).toBe(true);
    expect(passesFilters(pltr, { ...none, maxLoss: "400" })).toBe(false);
    expect(passesFilters(pltr, { ...none, maxLossGain: "6" })).toBe(true);
    expect(passesFilters(pltr, { ...none, minEv: "0" })).toBe(false);
    expect(passesFilters(pltr, { ...none, minToBe: "1" })).toBe(false); // 0.98 sigma is short of one
  });

  it("fails a limit on a number the row does not carry", () => {
    expect(passesFilters(null, { ...none, minEv: "0" })).toBe(false);
    expect(passesFilters({ ...pltr, to_breakeven_sigma: null }, { ...none, minToBe: "0.5" })).toBe(false);
  });
});

describe("the margin and IV/RV limits, and the built-in preset", () => {
  const ctx = { forecast: 0.436, ivRv: 1.3, buyer: false };
  const withBe = { ...pltr, breakeven_vol: 0.54 };

  it("reads the breakeven-vol margin for the structure's side", async () => {
    const { passesFilters: pass } = await import("./ScreenerTab");
    expect(pass(withBe, { ...none, minMargin: "10" }, ctx)).toBe(true); // 54.0 - 43.6 = 10.4
    expect(pass(withBe, { ...none, minMargin: "11" }, ctx)).toBe(false);
    expect(pass(withBe, { ...none, minMargin: "0" }, { ...ctx, buyer: true })).toBe(false); // a buyer wants it below
    expect(pass(withBe, { ...none, minMargin: "3" }, { ...ctx, forecast: null })).toBe(false);
  });

  it("limits IV/RV off the row", async () => {
    const { passesFilters: pass } = await import("./ScreenerTab");
    expect(pass(withBe, { ...none, minIvRv: "1.2" }, ctx)).toBe(true);
    expect(pass(withBe, { ...none, minIvRv: "1.5" }, ctx)).toBe(false);
  });

  it("ships a preset that keeps a structure like PLTR's on its margin, not its EV", async () => {
    const { BUILT_IN_PRESETS, passesFilters: pass } = await import("./ScreenerTab");
    const gems = BUILT_IN_PRESETS[0];
    expect(gems.name).toMatch(/Bot gems/);
    expect(gems.earnings).toBe("avoid");
    expect(gems.priceAt).toBe("natural");
    expect(gems.filters.minEv).toBe("");
    // PLTR's 175/170: 18 % loss, 5.5 : 1, +10.4 margin, IV/RV 1.3 -- but the
    // breakeven 0.98 sigma away clears the 0.9 bar.
    expect(pass(withBe, gems.filters, ctx)).toBe(true);
  });
});

describe("a run stored before the outcome carried its distance to breakeven", () => {
  it("is judged on the distance read off its legs, not failed for lacking it", async () => {
    const { passesFilters: pass } = await import("./ScreenerTab");
    const old = { ...pltr, to_breakeven_sigma: undefined };
    const ctx = { forecast: 0.436, ivRv: 1.3, buyer: false, toBeSigma: 0.98 };
    expect(pass(old, { ...none, minToBe: "0.9" }, ctx)).toBe(true);
    expect(pass(old, { ...none, minToBe: "1.0" }, ctx)).toBe(false);
  });
});

describe("the earnings-ok limit and the early-earnings preset", () => {
  it("takes out a row whose earnings rule failed and keeps one without a known date", async () => {
    const { passesFilters: pass } = await import("./ScreenerTab");
    const base = { forecast: 0.436, ivRv: 1.3, buyer: false };
    expect(pass(pltr, { ...none, earningsOk: "1" }, { ...base, earningsPassed: false })).toBe(false);
    expect(pass(pltr, { ...none, earningsOk: "1" }, { ...base, earningsPassed: true })).toBe(true);
    expect(pass(pltr, { ...none, earningsOk: "1" }, { ...base, earningsPassed: null })).toBe(true);
  });

  it("ships the early-earnings preset further out and asking more", async () => {
    const { BUILT_IN_PRESETS } = await import("./ScreenerTab");
    const [gems, early] = BUILT_IN_PRESETS;
    expect(early.name).toMatch(/early earnings/);
    expect(early.earnings).toBe("early");
    expect(early.filters.earningsOk).toBe("1");
    expect(Number(early.deltaMax)).toBeLessThan(Number(gems.deltaMax));
    expect(Number(early.filters.minMargin)).toBeGreaterThan(Number(gems.filters.minMargin));
    expect(Number(early.filters.minToBe)).toBeGreaterThan(Number(gems.filters.minToBe));
  });
});
