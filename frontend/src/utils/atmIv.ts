import type { ChainResponse } from "../types/options";

/** A side's IV, only if someone bids for the contract: an unbid quote's IV
 * is solved from an ask or an old print. Mirrors backend iv_context.bid_iv. */
function bidIv(q: { iv?: number | null; bid?: number | null } | null | undefined): number | null {
  return q && (q.iv ?? 0) > 0 && (q.bid ?? 0) > 0 ? (q.iv as number) : null;
}

/** The at-the-money implied volatility of a chain: the mean of call and put
 * IV at the strike nearest the spot, or whichever side is quoted there --
 * bid quotes only. Null when the chain carries no such IV (a replayed chain
 * before its solver ran, an expiry on its last day). Mirrors backend
 * optimize.atm_sigma; what it returns is recorded into the IV history. */
export function atmIv(chain: ChainResponse | null): number | null {
  if (!chain || chain.rows.length === 0) return null;
  const quoted = chain.rows.filter((r) => bidIv(r.call) != null || bidIv(r.put) != null);
  if (quoted.length === 0) return null;
  let best = quoted[0];
  for (const r of quoted) if (Math.abs(r.strike - chain.spot) < Math.abs(best.strike - chain.spot)) best = r;
  const ivs = [bidIv(best.call), bidIv(best.put)].filter((v): v is number => v != null);
  return ivs.length ? ivs.reduce((a, b) => a + b, 0) / ivs.length : null;
}

/** The chain, only if it is `symbol`'s. On a symbol switch the widget
 * renders once with the new symbol and the previous chain still in state
 * (the chain hook clears it in an effect, a render later) -- and anything
 * reading the two together in that render pairs one symbol with the other's
 * numbers. That is how AMZN was recorded at TLT's 17 % IV on 2026-10-01. */
export function chainOf(symbol: string | null, chain: ChainResponse | null): ChainResponse | null {
  if (!symbol || !chain) return null;
  return chain.underlying.toUpperCase() === symbol.toUpperCase() ? chain : null;
}

/** One standard deviation of the move the market prices to `dte` days
 * out: spot × σ × √T. */
export function impliedMove(spot: number | null, iv: number | null, dte: number | null): number | null {
  if (!spot || !iv || !dte || dte <= 0) return null;
  return spot * iv * Math.sqrt(dte / 365);
}
