/** Choosing a spread's legs from the chain: the default pick when a
 * strategy, expiry or symbol changes, and how a click on a strike cell
 * reshapes the selection. Pure, so the widget's state logic is testable
 * by reading rather than clicking. */

import {
  DEBIT_STRATEGIES,
  SINGLE_LEG_STRATEGIES,
  type ChainResponse,
  type OptionKind,
  type StrikeRow,
  type Strategy,
} from "../../types/options";
import { legKey, type LegSelection } from "./ChainTable";

export interface VerticalLegs {
  long: number;
  short: number;
}

export interface CondorLegs {
  put_long: number;
  put_short: number;
  call_short: number;
  call_long: number;
}

/** A long call or put: one bought contract. */
export interface SingleLegs {
  strike: number;
}

export type Legs = VerticalLegs | CondorLegs | SingleLegs;

export function isCondor(legs: Legs): legs is CondorLegs {
  return "put_short" in legs;
}

export function isSingle(legs: Legs): legs is SingleLegs {
  return "strike" in legs;
}

/** Delta the default short leg aims for. ~0.30 is the usual "one standard
 * deviation-ish" vertical; a condor's wings sit further out. */
const SHORT_DELTA_VERTICAL = 0.3;
const SHORT_DELTA_CONDOR = 0.2;
/** Without greeks (0DTE, unconverged IV), the short leg goes this far OTM. */
const FALLBACK_OTM_PCT = 0.03;

export function strategyKind(strategy: Strategy): OptionKind | "both" {
  if (strategy === "iron_condor") return "both";
  return strategy === "long_call" || strategy === "bull_call" || strategy === "bear_call" ? "call" : "put";
}

function quoted(rows: StrikeRow[], kind: OptionKind): StrikeRow[] {
  return rows.filter((r) => (kind === "call" ? r.call : r.put) !== null);
}

function nearestIndex(rows: StrikeRow[], price: number): number {
  let best = 0;
  for (let i = 1; i < rows.length; i++) {
    if (Math.abs(rows[i].strike - price) < Math.abs(rows[best].strike - price)) best = i;
  }
  return best;
}

/** Index of the OTM row whose |delta| is nearest `target`; without deltas,
 * the row nearest spot ± FALLBACK_OTM_PCT. */
function otmShortIndex(rows: StrikeRow[], kind: OptionKind, spot: number, target: number): number {
  const otm = rows.map((r, i) => ({ r, i })).filter(({ r }) => (kind === "call" ? r.strike > spot : r.strike < spot));
  if (otm.length === 0) return nearestIndex(rows, spot);
  const withDelta = otm.filter(({ r }) => (kind === "call" ? r.call?.delta : r.put?.delta) != null);
  if (withDelta.length > 0) {
    let best = withDelta[0];
    for (const cand of withDelta) {
      const d = Math.abs(Math.abs((kind === "call" ? cand.r.call!.delta : cand.r.put!.delta) as number) - target);
      const bd = Math.abs(Math.abs((kind === "call" ? best.r.call!.delta : best.r.put!.delta) as number) - target);
      if (d < bd) best = cand;
    }
    return best.i;
  }
  const targetPrice = kind === "call" ? spot * (1 + FALLBACK_OTM_PCT) : spot * (1 - FALLBACK_OTM_PCT);
  return nearestIndex(rows, targetPrice);
}

function clampIndex(i: number, rows: StrikeRow[]): number {
  return Math.min(Math.max(i, 0), rows.length - 1);
}

/** The default legs for a strategy on this chain, `width` strikes wide.
 * Null when the chain cannot support it (too few quoted strikes). */
export function defaultLegs(strategy: Strategy, chain: ChainResponse, width: number): Legs | null {
  const w = Math.max(1, Math.floor(width));
  if (strategy === "iron_condor") {
    const puts = quoted(chain.rows, "put");
    const calls = quoted(chain.rows, "call");
    if (puts.length < w + 1 || calls.length < w + 1) return null;
    const ps = otmShortIndex(puts, "put", chain.spot, SHORT_DELTA_CONDOR);
    const cs = otmShortIndex(calls, "call", chain.spot, SHORT_DELTA_CONDOR);
    const pl = clampIndex(ps - w, puts);
    const cl = clampIndex(cs + w, calls);
    const legs = {
      put_long: puts[pl].strike,
      put_short: puts[ps].strike,
      call_short: calls[cs].strike,
      call_long: calls[cl].strike,
    };
    return legs.put_long < legs.put_short && legs.put_short < legs.call_short && legs.call_short < legs.call_long
      ? legs
      : null;
  }
  const kind = strategyKind(strategy) as OptionKind;
  const rows = quoted(chain.rows, kind);
  if (SINGLE_LEG_STRATEGIES.has(strategy)) {
    // An outright long starts at the money.
    return rows.length > 0 ? { strike: rows[nearestIndex(rows, chain.spot)].strike } : null;
  }
  if (rows.length < w + 1) return null;
  if (DEBIT_STRATEGIES.has(strategy)) {
    // Long the at-the-money contract, short `width` strikes out-of-the-money.
    const longIdx = nearestIndex(rows, chain.spot);
    const shortIdx = clampIndex(kind === "call" ? longIdx + w : longIdx - w, rows);
    if (shortIdx === longIdx) return null;
    return { long: rows[longIdx].strike, short: rows[shortIdx].strike };
  }
  // Credit: short the ~0.30-delta contract, long `width` strikes further out.
  const shortIdx = otmShortIndex(rows, kind, chain.spot, SHORT_DELTA_VERTICAL);
  const longIdx = clampIndex(kind === "call" ? shortIdx + w : shortIdx - w, rows);
  if (longIdx === shortIdx) return null;
  return { long: rows[longIdx].strike, short: rows[shortIdx].strike };
}

/** A click on a strike cell. Verticals: the click lands on whichever role
 * keeps the spread's shape (long below short for the bullish pair, above
 * for the bearish pair); clicking the other kind is ignored. Condor: the
 * put wing's higher strike is the short, the call wing's lower one. When a
 * click would collapse the spread, the other leg is pushed out by one
 * strike. */
export function applyPick(
  strategy: Strategy,
  legs: Legs | null,
  chain: ChainResponse,
  kind: OptionKind,
  strike: number,
): Legs | null {
  const rows = quoted(chain.rows, kind);
  const idx = rows.findIndex((r) => r.strike === strike);
  if (idx === -1) return legs;
  const step = (i: number) => rows[clampIndex(i, rows)].strike;

  if (strategy === "iron_condor") {
    const current = legs && isCondor(legs) ? legs : defaultLegs(strategy, chain, 2);
    if (!current || !isCondor(current)) return legs;
    if (kind === "put") {
      if (strike < current.put_short) return { ...current, put_long: strike };
      const put_long = current.put_long < strike ? current.put_long : step(idx - 1);
      return { ...current, put_short: strike, put_long };
    }
    if (strike > current.call_short) return { ...current, call_long: strike };
    const call_long = current.call_long > strike ? current.call_long : step(idx + 1);
    return { ...current, call_short: strike, call_long };
  }

  if (kind !== strategyKind(strategy)) return legs;
  if (SINGLE_LEG_STRATEGIES.has(strategy)) return { strike };
  const longBelow = strategy === "bull_call" || strategy === "bull_put";
  const current = legs && !isCondor(legs) && !isSingle(legs) ? legs : null;
  if (!current) {
    // First pick: treat it as the short leg and put the long one strike out.
    const long = step(longBelow ? idx - 1 : idx + 1);
    return long === strike ? null : { long, short: strike };
  }
  const isLongSide = longBelow ? strike < current.short : strike > current.short;
  if (isLongSide) return { ...current, long: strike };
  // It is the short leg; make sure the long stays on its side of it.
  const longOk = longBelow ? current.long < strike : current.long > strike;
  const long = longOk ? current.long : step(longBelow ? idx - 1 : idx + 1);
  return long === strike ? legs : { long, short: strike };
}

export function selectionOf(strategy: Strategy, legs: Legs | null): LegSelection {
  const map: LegSelection = new Map();
  if (!legs) return map;
  if (isCondor(legs)) {
    map.set(legKey("put", legs.put_long), "long");
    map.set(legKey("put", legs.put_short), "short");
    map.set(legKey("call", legs.call_short), "short");
    map.set(legKey("call", legs.call_long), "long");
    return map;
  }
  const kind = strategyKind(strategy) as OptionKind;
  if (isSingle(legs)) {
    map.set(legKey(kind, legs.strike), "long");
    return map;
  }
  map.set(legKey(kind, legs.long), "long");
  map.set(legKey(kind, legs.short), "short");
  return map;
}
