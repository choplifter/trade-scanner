import type { ChainResponse } from "../types/options";

/** The at-the-money implied volatility of a chain: the mean of call and put
 * IV at the strike nearest the spot, or whichever side is quoted there.
 * Null when the chain carries no IV (a replayed chain before its solver
 * ran, an expiry on its last day). Mirrors backend optimize.atm_sigma. */
export function atmIv(chain: ChainResponse | null): number | null {
  if (!chain || chain.rows.length === 0) return null;
  const quoted = chain.rows.filter((r) => (r.call?.iv ?? 0) > 0 || (r.put?.iv ?? 0) > 0);
  if (quoted.length === 0) return null;
  let best = quoted[0];
  for (const r of quoted) if (Math.abs(r.strike - chain.spot) < Math.abs(best.strike - chain.spot)) best = r;
  const ivs = [best.call?.iv, best.put?.iv].filter((v): v is number => v != null && v > 0);
  return ivs.length ? ivs.reduce((a, b) => a + b, 0) / ivs.length : null;
}

/** One standard deviation of the move the market prices to `dte` days
 * out: spot × σ × √T. */
export function impliedMove(spot: number | null, iv: number | null, dte: number | null): number | null {
  if (!spot || !iv || !dte || dte <= 0) return null;
  return spot * iv * Math.sqrt(dte / 365);
}
