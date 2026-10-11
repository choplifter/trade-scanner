import { describe, expect, it } from "vitest";

import type { Payoff } from "../types/options";
import { legCurves } from "./blackScholes";

// PLTR's bull put: 175 sold at 0.725, 170 bought at 0.42 -- a 0.305 credit.
const prices = [160, 165, 170, 172.5, 175, 180, 200];
const payoff: Payoff = {
  prices,
  at_expiry: prices.map((p) => {
    const short = -Math.max(175 - p, 0) + 0.725;
    const long = Math.max(170 - p, 0) - 0.42;
    return (short + long) * 100;
  }),
  today: null,
  breakevens: [174.695],
  max_profit: 30.5,
  max_loss: -469.5,
  spot: 208.73,
  expiry: "2026-11-20",
  multiplier: 100,
  net_price: -0.305,
  legs: [
    { kind: "put", strike: 175, side: "sell", ratio: 1, expiry: "2026-11-20", iv: 0.6, price: 0.725 },
    { kind: "put", strike: 170, side: "buy", ratio: 1, expiry: "2026-11-20", iv: 0.62, price: 0.42 },
  ],
};

describe("the risk chart's per-leg curves", () => {
  it("draws each leg from its own entry, and the legs add up to the whole", () => {
    const curves = legCurves(payoff)!;
    expect(curves).toHaveLength(2);
    const [short, long] = curves.map((c) => c.values);
    expect(short[prices.indexOf(200)]).toBeCloseTo(72.5); // the sold put expires worthless: its premium kept
    expect(long[prices.indexOf(200)]).toBeCloseTo(-42); // the bought one: its premium lost
    expect(short[prices.indexOf(160)]).toBeCloseTo(-1427.5); // 15 in the money, less the premium
    prices.forEach((_, i) => expect(short[i] + long[i]).toBeCloseTo(payoff.at_expiry[i]));
  });

  it("draws nothing when a leg carries no entry", () => {
    expect(legCurves({ ...payoff, legs: [{ ...payoff.legs![0], price: null }, payoff.legs![1]] })).toBeNull();
  });
});
