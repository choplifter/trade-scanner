/**
 * Black-Scholes in the browser, for the risk chart's what-if sliders. The
 * backend draws the at-expiry and today curves (app/options/payoff.py);
 * this reprices the same legs at a later moment and a shifted implied
 * volatility so the sliders answer instantly, without a round trip per
 * notch. Same simplifications as the backend: no rate, no dividends, no
 * skew model -- one IV per leg, scaled by the same factor.
 */

import type { Payoff, PayoffLegOut } from "../types/options";

const MS_PER_YEAR = 365 * 24 * 3600 * 1000;

/** Abramowitz-Stegun 7.1.26 -- plenty for a chart. */
function normCdf(x: number): number {
  const sign = x < 0 ? -1 : 1;
  const ax = Math.abs(x) / Math.SQRT2;
  const t = 1 / (1 + 0.3275911 * ax);
  const poly = t * (0.254829592 + t * (-0.284496736 + t * (1.421413741 + t * (-1.453152027 + t * 1.061405429))));
  const erf = 1 - poly * Math.exp(-ax * ax);
  return 0.5 * (1 + sign * erf);
}

export function bsPrice(kind: "call" | "put", spot: number, strike: number, years: number, sigma: number): number {
  if (years <= 0 || sigma <= 0) return intrinsic(kind, spot, strike);
  const sqrtT = Math.sqrt(years);
  const d1 = (Math.log(spot / strike) + 0.5 * sigma * sigma * years) / (sigma * sqrtT);
  const d2 = d1 - sigma * sqrtT;
  if (kind === "call") return spot * normCdf(d1) - strike * normCdf(d2);
  return strike * normCdf(-d2) - spot * normCdf(-d1);
}

export function intrinsic(kind: "call" | "put" | "stock", spot: number, strike: number): number {
  if (kind === "stock") return spot - strike;
  return kind === "call" ? Math.max(spot - strike, 0) : Math.max(strike - spot, 0);
}

/** The moment a contract stops trading: 16:00 New York. Written as 20:00
 * UTC, which is exact in summer and an hour early in winter -- for a
 * slider whose notches are whole hours that is close enough, and it keeps
 * a timezone table out of the bundle. Mirrors payoff._expiry_moment. */
export function expiryMoment(expiry: string): number {
  return Date.parse(`${expiry}T20:00:00Z`);
}

function legValue(leg: PayoffLegOut, spot: number, atMs: number, ivFactor: number): number | null {
  if (leg.kind === "stock") return intrinsic("stock", spot, leg.strike);
  const years = leg.expiry ? (expiryMoment(leg.expiry) - atMs) / MS_PER_YEAR : 0;
  if (years <= 0) return intrinsic(leg.kind, spot, leg.strike);
  if (leg.iv == null || leg.iv <= 0) return null;
  return bsPrice(leg.kind, spot, leg.strike, years, leg.iv * ivFactor);
}

/**
 * The position's P&L over the payoff's price grid at `hoursAhead` hours
 * after the payoff's own valuation moment, with every leg's IV multiplied
 * by `ivFactor`. Null when a leg has no IV (the backend's today curve is
 * null then too) or the payoff predates the fields this needs.
 */
export function scenarioCurve(payoff: Payoff, hoursAhead: number, ivFactor: number): number[] | null {
  if (!payoff.as_of || !payoff.legs || payoff.legs.length === 0 || payoff.net_price == null) return null;
  const atMs = Date.parse(payoff.as_of) + hoursAhead * 3600 * 1000;
  const out: number[] = [];
  for (const price of payoff.prices) {
    let total = 0;
    for (const leg of payoff.legs) {
      const value = legValue(leg, price, atMs, ivFactor);
      if (value == null) return null;
      total += (leg.side === "buy" ? 1 : -1) * leg.ratio * value;
    }
    out.push(Math.round((total - payoff.net_price) * payoff.multiplier * 100) / 100);
  }
  return out;
}

/** Whole hours from the payoff's valuation moment to its first expiry. */
export function hoursToExpiry(payoff: Payoff): number {
  if (!payoff.as_of) return 0;
  return Math.max(0, Math.floor((expiryMoment(payoff.expiry) - Date.parse(payoff.as_of)) / 3600000));
}

/** The average IV across the option legs, for the slider's label. */
export function meanIv(payoff: Payoff): number | null {
  const ivs = (payoff.legs ?? []).map((leg) => leg.iv).filter((iv): iv is number => iv != null && iv > 0);
  if (ivs.length === 0) return null;
  return ivs.reduce((a, b) => a + b, 0) / ivs.length;
}
