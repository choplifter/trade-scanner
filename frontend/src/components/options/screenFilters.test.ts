import { describe, expect, it } from "vitest";

import type { ScreenOutcome } from "../../types/options";
import { passesFilters } from "./ScreenerTab";

const none = { maxLossProb: "", minProfit: "", maxLoss: "", maxLossGain: "", minEv: "", minToBe: "" };
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
