/** Choosing a strategy's legs from the chain: the default pick when a
 * strategy, expiry or symbol changes, and how a click on a strike cell
 * reshapes the selection. Pure, so the widget's state logic is testable
 * by reading rather than clicking. */

import {
  DEBIT_STRATEGIES,
  INCOME_STRATEGIES,
  LEGS_STRATEGIES,
  SINGLE_LEG_STRATEGIES,
  TIME_STRATEGIES,
  type ChainResponse,
  type OptionKind,
  type ShortTarget,
  type SpreadTicketRequest,
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

/** A long call or put, a straddle (one strike, both kinds), a covered
 * call or a cash-secured put: one strike. */
export interface SingleLegs {
  strike: number;
}

export interface StrangleLegs {
  put: number;
  call: number;
}

/** Wings bought, body sold twice. */
export interface ButterflyLegs {
  low: number;
  mid: number;
  high: number;
}

export interface IronButterflyLegs {
  put_long: number;
  body: number;
  call_long: number;
}

/** Calendar (same strike) or diagonal (two strikes): the short leg in the
 * ticket's expiry, the long leg in `long_expiry`. */
export interface TimeLegs {
  short_strike: number;
  long_strike: number;
  long_expiry: string;
}

export type Legs = VerticalLegs | CondorLegs | SingleLegs | StrangleLegs | ButterflyLegs | IronButterflyLegs | TimeLegs;

export function isCondor(legs: Legs): legs is CondorLegs {
  return "put_short" in legs;
}
export function isIronButterfly(legs: Legs): legs is IronButterflyLegs {
  return "body" in legs;
}
export function isButterfly(legs: Legs): legs is ButterflyLegs {
  return "mid" in legs;
}
export function isStrangle(legs: Legs): legs is StrangleLegs {
  return "put" in legs && "call" in legs;
}
export function isTime(legs: Legs): legs is TimeLegs {
  return "long_expiry" in legs;
}
export function isSingle(legs: Legs): legs is SingleLegs {
  return "strike" in legs;
}
export function isVertical(legs: Legs): legs is VerticalLegs {
  return "long" in legs && "short" in legs;
}

/** Delta the default short leg aims for. ~0.30 is the usual "one standard
 * deviation-ish" vertical; a condor's wings sit further out; a strangle's
 * legs a little closer than that. */
const SHORT_DELTA_VERTICAL = 0.3;
const SHORT_DELTA_CONDOR = 0.2;
const STRANGLE_DELTA = 0.25;
/** Without greeks (0DTE, unconverged IV), the short leg goes this far OTM. */
const FALLBACK_OTM_PCT = 0.03;

/** Extra choices the picker needs beyond the strategy: the kind a
 * calendar/diagonal trades, the later expiry and its chain, and which of
 * the two expiries a click is meant for. */
export interface PickContext {
  timeKind?: OptionKind;
  longExpiry?: string | null;
  longChain?: ChainResponse | null;
  picking?: "short" | "long";
  /** How far out the short leg(s) go for the current strategy (see
   * types/options.ts ShortTarget); the delta constants above when unset. */
  shortTarget?: ShortTarget | null;
}

export function strategyKind(strategy: Strategy, timeKind: OptionKind = "call"): OptionKind | "both" {
  if (
    strategy === "iron_condor" ||
    strategy === "long_straddle" ||
    strategy === "long_strangle" ||
    strategy === "iron_butterfly"
  ) {
    return "both";
  }
  if (TIME_STRATEGIES.has(strategy)) return timeKind;
  if (
    strategy === "bull_call" ||
    strategy === "bear_call" ||
    strategy === "long_call" ||
    strategy === "call_butterfly" ||
    strategy === "covered_call"
  ) {
    return "call";
  }
  return "put";
}

function quoted(rows: StrikeRow[], kind: OptionKind): StrikeRow[] {
  return rows.filter((r) => (kind === "call" ? r.call : r.put) !== null);
}

function bothQuoted(rows: StrikeRow[]): StrikeRow[] {
  return rows.filter((r) => r.call !== null && r.put !== null);
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

/** Index of the n-th out-of-the-money row counted from the spot (0 = the
 * first strike outside it), clamped to the chain. */
function offsetIndex(rows: StrikeRow[], kind: OptionKind, spot: number, n: number): number {
  const otm = rows
    .map((r, i) => ({ r, i }))
    .filter(({ r }) => (kind === "call" ? r.strike > spot : r.strike < spot))
    .sort((a, b) => Math.abs(a.r.strike - spot) - Math.abs(b.r.strike - spot));
  if (otm.length === 0) return nearestIndex(rows, spot);
  return otm[Math.min(Math.max(0, Math.floor(n)), otm.length - 1)].i;
}

/** Where a short leg goes: the picker's short-target setting when there
 * is one (delta or strike offset), else the strategy's delta constant. */
function shortIndex(rows: StrikeRow[], kind: OptionKind, spot: number, fallbackDelta: number, ctx: PickContext): number {
  const target = ctx.shortTarget;
  if (target && target.mode === "offset") return offsetIndex(rows, kind, spot, target.value);
  return otmShortIndex(rows, kind, spot, target && target.value > 0 ? target.value : fallbackDelta);
}

function clampIndex(i: number, rows: StrikeRow[]): number {
  return Math.min(Math.max(i, 0), rows.length - 1);
}

/** The default legs for a strategy on this chain, `width` strikes wide.
 * Null when the chain cannot support it (too few quoted strikes). */
export function defaultLegs(strategy: Strategy, chain: ChainResponse, width: number, ctx: PickContext = {}): Legs | null {
  const w = Math.max(1, Math.floor(width));
  const timeKind = ctx.timeKind ?? "call";

  if (strategy === "iron_condor") {
    const puts = quoted(chain.rows, "put");
    const calls = quoted(chain.rows, "call");
    if (puts.length < w + 1 || calls.length < w + 1) return null;
    const ps = shortIndex(puts, "put", chain.spot, SHORT_DELTA_CONDOR, ctx);
    const cs = shortIndex(calls, "call", chain.spot, SHORT_DELTA_CONDOR, ctx);
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

  if (strategy === "long_straddle") {
    const rows = bothQuoted(chain.rows);
    return rows.length > 0 ? { strike: rows[nearestIndex(rows, chain.spot)].strike } : null;
  }

  if (strategy === "long_strangle") {
    const puts = quoted(chain.rows, "put");
    const calls = quoted(chain.rows, "call");
    if (puts.length === 0 || calls.length === 0) return null;
    const legs = {
      put: puts[shortIndex(puts, "put", chain.spot, STRANGLE_DELTA, ctx)].strike,
      call: calls[shortIndex(calls, "call", chain.spot, STRANGLE_DELTA, ctx)].strike,
    };
    return legs.put < legs.call ? legs : null;
  }

  if (strategy === "iron_butterfly") {
    const rows = bothQuoted(chain.rows);
    if (rows.length < 2 * w + 1) return null;
    const body = nearestIndex(rows, chain.spot);
    const legs = {
      put_long: rows[clampIndex(body - w, rows)].strike,
      body: rows[body].strike,
      call_long: rows[clampIndex(body + w, rows)].strike,
    };
    return legs.put_long < legs.body && legs.body < legs.call_long ? legs : null;
  }

  if (strategy === "call_butterfly" || strategy === "put_butterfly") {
    const kind: OptionKind = strategy === "call_butterfly" ? "call" : "put";
    const rows = quoted(chain.rows, kind);
    if (rows.length < 2 * w + 1) return null;
    const mid = nearestIndex(rows, chain.spot);
    const legs = {
      low: rows[clampIndex(mid - w, rows)].strike,
      mid: rows[mid].strike,
      high: rows[clampIndex(mid + w, rows)].strike,
    };
    return legs.low < legs.mid && legs.mid < legs.high ? legs : null;
  }

  if (TIME_STRATEGIES.has(strategy)) {
    if (!ctx.longExpiry) return null;
    const rows = quoted(chain.rows, timeKind);
    if (rows.length === 0) return null;
    const longRows = ctx.longChain ? quoted(ctx.longChain.rows, timeKind) : rows;
    if (longRows.length === 0) return null;
    if (strategy === "calendar") {
      // One strike both expiries quote, nearest the spot.
      const shared = rows.filter((r) => longRows.some((l) => l.strike === r.strike));
      const pool = shared.length > 0 ? shared : rows;
      const strike = pool[nearestIndex(pool, chain.spot)].strike;
      return { short_strike: strike, long_strike: strike, long_expiry: ctx.longExpiry };
    }
    const shortStrike = rows[otmShortIndex(rows, timeKind, chain.spot, SHORT_DELTA_VERTICAL)].strike;
    const longStrike = longRows[nearestIndex(longRows, chain.spot)].strike;
    if (shortStrike === longStrike) return null;
    return { short_strike: shortStrike, long_strike: longStrike, long_expiry: ctx.longExpiry };
  }

  const kind = strategyKind(strategy, timeKind) as OptionKind;
  const rows = quoted(chain.rows, kind);
  if (INCOME_STRATEGIES.has(strategy)) {
    // Written ~0.30 delta out of the money.
    return rows.length > 0 ? { strike: rows[shortIndex(rows, kind, chain.spot, SHORT_DELTA_VERTICAL, ctx)].strike } : null;
  }
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
  // Credit: short the ~0.30-delta contract (or the picker's short-target
  // setting), long `width` strikes further out.
  const shortIdx = shortIndex(rows, kind, chain.spot, SHORT_DELTA_VERTICAL, ctx);
  const longIdx = clampIndex(kind === "call" ? shortIdx + w : shortIdx - w, rows);
  if (longIdx === shortIdx) return null;
  return { long: rows[longIdx].strike, short: rows[shortIdx].strike };
}

/** A click on a strike cell. Verticals: the click lands on whichever role
 * keeps the spread's shape (long below short for the bullish pair, above
 * for the bearish pair); clicking the other kind is ignored. Condor: the
 * put wing's higher strike is the short, the call wing's lower one.
 * Butterflies: a click outside the wings moves the nearer wing, inside
 * them moves the body. When a click would collapse the spread, the other
 * leg is pushed out by one strike. Calendar: the strike; diagonal: the
 * leg of the expiry being picked. */
export function applyPick(
  strategy: Strategy,
  legs: Legs | null,
  chain: ChainResponse,
  kind: OptionKind,
  strike: number,
  ctx: PickContext = {},
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

  if (strategy === "long_straddle") return { strike };

  if (strategy === "long_strangle") {
    const current = legs && isStrangle(legs) ? legs : defaultLegs(strategy, chain, 1);
    if (!current || !isStrangle(current)) return { put: strike, call: strike };
    if (kind === "put") return strike < current.call ? { ...current, put: strike } : { put: strike, call: step(idx + 1) };
    return strike > current.put ? { ...current, call: strike } : { put: step(idx - 1), call: strike };
  }

  if (strategy === "call_butterfly" || strategy === "put_butterfly") {
    if (kind !== strategyKind(strategy)) return legs;
    const current = legs && isButterfly(legs) ? legs : defaultLegs(strategy, chain, 2);
    if (!current || !isButterfly(current)) return legs;
    if (strike === current.mid) return legs;
    if (strike <= current.low) return { ...current, low: strike };
    if (strike >= current.high) return { ...current, high: strike };
    // Between the wings: a new body, wings kept where they are.
    return { ...current, mid: strike };
  }

  if (strategy === "iron_butterfly") {
    const current = legs && isIronButterfly(legs) ? legs : defaultLegs(strategy, chain, 2);
    if (!current || !isIronButterfly(current)) return legs;
    if (strike === current.body) return legs;
    if (kind === "put" && strike < current.body) return { ...current, put_long: strike };
    if (kind === "call" && strike > current.body) return { ...current, call_long: strike };
    // A put above the body or a call below it: move the body there and
    // keep the wings at their old distances.
    const below = current.body - current.put_long;
    const above = current.call_long - current.body;
    return { put_long: strike - below, body: strike, call_long: strike + above };
  }

  if (TIME_STRATEGIES.has(strategy)) {
    if (kind !== (ctx.timeKind ?? "call")) return legs;
    const current = legs && isTime(legs) ? legs : defaultLegs(strategy, chain, 1, ctx);
    if (!current || !isTime(current)) return legs;
    if (strategy === "calendar") return { ...current, short_strike: strike, long_strike: strike };
    if (ctx.picking === "long") return strike === current.short_strike ? legs : { ...current, long_strike: strike };
    return strike === current.long_strike ? legs : { ...current, short_strike: strike };
  }

  if (kind !== strategyKind(strategy)) return legs;
  if (SINGLE_LEG_STRATEGIES.has(strategy)) return { strike };
  const longBelow = strategy === "bull_call" || strategy === "bull_put";
  const current = legs && isVertical(legs) ? legs : null;
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

/** The chain's highlight for the legs: `body` is the butterfly's doubled
 * short. For a calendar/diagonal only the leg of the expiry on screen is
 * marked (see `picking`). */
export function selectionOf(strategy: Strategy, legs: Legs | null, ctx: PickContext = {}): LegSelection {
  const map: LegSelection = new Map();
  if (!legs) return map;
  if (isCondor(legs)) {
    map.set(legKey("put", legs.put_long), "long");
    map.set(legKey("put", legs.put_short), "short");
    map.set(legKey("call", legs.call_short), "short");
    map.set(legKey("call", legs.call_long), "long");
    return map;
  }
  if (isIronButterfly(legs)) {
    map.set(legKey("put", legs.put_long), "long");
    map.set(legKey("put", legs.body), "short");
    map.set(legKey("call", legs.body), "short");
    map.set(legKey("call", legs.call_long), "long");
    return map;
  }
  if (isButterfly(legs)) {
    const kind = strategyKind(strategy) as OptionKind;
    map.set(legKey(kind, legs.low), "long");
    map.set(legKey(kind, legs.mid), "body");
    map.set(legKey(kind, legs.high), "long");
    return map;
  }
  if (isStrangle(legs)) {
    map.set(legKey("put", legs.put), "long");
    map.set(legKey("call", legs.call), "long");
    return map;
  }
  if (isTime(legs)) {
    const kind = ctx.timeKind ?? "call";
    if (ctx.picking === "long") map.set(legKey(kind, legs.long_strike), "long");
    else map.set(legKey(kind, legs.short_strike), "short");
    return map;
  }
  if (isSingle(legs)) {
    if (strategy === "long_straddle") {
      map.set(legKey("put", legs.strike), "long");
      map.set(legKey("call", legs.strike), "long");
      return map;
    }
    const kind = strategyKind(strategy) as OptionKind;
    map.set(legKey(kind, legs.strike), INCOME_STRATEGIES.has(strategy) ? "short" : "long");
    return map;
  }
  const kind = strategyKind(strategy) as OptionKind;
  map.set(legKey(kind, legs.long), "long");
  map.set(legKey(kind, legs.short), "short");
  return map;
}

/** The strikes a set of legs occupies, for the chart's level lines. */
export function legLevels(strategy: Strategy, legs: Legs, ctx: PickContext = {}): { label: string; price: number; role: "long" | "short" }[] {
  if (isCondor(legs)) {
    return [
      { label: `Put long ${legs.put_long}`, price: legs.put_long, role: "long" },
      { label: `Put short ${legs.put_short}`, price: legs.put_short, role: "short" },
      { label: `Call short ${legs.call_short}`, price: legs.call_short, role: "short" },
      { label: `Call long ${legs.call_long}`, price: legs.call_long, role: "long" },
    ];
  }
  if (isIronButterfly(legs)) {
    return [
      { label: `Put long ${legs.put_long}`, price: legs.put_long, role: "long" },
      { label: `Body ${legs.body}`, price: legs.body, role: "short" },
      { label: `Call long ${legs.call_long}`, price: legs.call_long, role: "long" },
    ];
  }
  if (isButterfly(legs)) {
    return [
      { label: `Wing ${legs.low}`, price: legs.low, role: "long" },
      { label: `Body ${legs.mid}`, price: legs.mid, role: "short" },
      { label: `Wing ${legs.high}`, price: legs.high, role: "long" },
    ];
  }
  if (isStrangle(legs)) {
    return [
      { label: `Put ${legs.put}`, price: legs.put, role: "long" },
      { label: `Call ${legs.call}`, price: legs.call, role: "long" },
    ];
  }
  if (isTime(legs)) {
    const out: { label: string; price: number; role: "long" | "short" }[] = [
      { label: `Short ${legs.short_strike}`, price: legs.short_strike, role: "short" },
    ];
    if (legs.long_strike !== legs.short_strike) out.push({ label: `Long ${legs.long_strike}`, price: legs.long_strike, role: "long" });
    return out;
  }
  if (isSingle(legs)) {
    const role = INCOME_STRATEGIES.has(strategy) ? "short" : "long";
    return [{ label: `${role === "short" ? "Short" : "Long"} ${legs.strike}`, price: legs.strike, role }];
  }
  void ctx;
  return [
    { label: `Long ${legs.long}`, price: legs.long, role: "long" },
    { label: `Short ${legs.short}`, price: legs.short, role: "short" },
  ];
}


/** The inverse of SpreadTicket's `ticketFor`: a wire ticket back into the
 * widget's own leg state, so a structure that came from somewhere else --
 * today the AI suggestion -- can be loaded into the ticket instead of being
 * re-picked by hand.
 *
 * Returns the long expiry alongside, because a calendar or diagonal needs
 * the widget to load a second chain before its legs mean anything. `null`
 * when the ticket does not describe the strategy it claims to; the caller
 * treats that as "cannot load" rather than guessing, since a half-applied
 * structure is worse than none. */
export function legsFromTicket(
  strategy: Strategy,
  ticket: SpreadTicketRequest,
): { legs: Legs; longExpiry?: string } | null {
  if (!LEGS_STRATEGIES.has(strategy)) {
    if (SINGLE_LEG_STRATEGIES.has(strategy)) {
      return ticket.long_strike != null ? { legs: { strike: ticket.long_strike } } : null;
    }
    if (strategy === "iron_condor") {
      const { put_long_strike, put_short_strike, call_short_strike, call_long_strike } = ticket;
      if (put_long_strike == null || put_short_strike == null || call_short_strike == null || call_long_strike == null) {
        return null;
      }
      return {
        legs: {
          put_long: put_long_strike,
          put_short: put_short_strike,
          call_short: call_short_strike,
          call_long: call_long_strike,
        },
      };
    }
    if (ticket.long_strike == null || ticket.short_strike == null) return null;
    return { legs: { long: ticket.long_strike, short: ticket.short_strike } };
  }

  const list = ticket.legs ?? [];
  const strikeOf = (kind: OptionKind, side: "buy" | "sell") =>
    list.find((leg) => leg.kind === kind && leg.side === side)?.strike;

  if (strategy === "long_straddle" || strategy === "covered_call" || strategy === "cash_secured_put") {
    const strike = list[0]?.strike;
    return strike == null ? null : { legs: { strike } };
  }
  if (strategy === "long_strangle") {
    const put = strikeOf("put", "buy");
    const call = strikeOf("call", "buy");
    return put == null || call == null ? null : { legs: { put, call } };
  }
  if (strategy === "call_butterfly" || strategy === "put_butterfly") {
    const strikes = list.map((leg) => leg.strike).sort((a, b) => a - b);
    if (strikes.length !== 3) return null;
    return { legs: { low: strikes[0], mid: strikes[1], high: strikes[2] } };
  }
  if (strategy === "iron_butterfly") {
    const put_long = strikeOf("put", "buy");
    const body = strikeOf("put", "sell");
    const call_long = strikeOf("call", "buy");
    return put_long == null || body == null || call_long == null ? null : { legs: { put_long, body, call_long } };
  }
  if (TIME_STRATEGIES.has(strategy)) {
    const short = list.find((leg) => leg.side === "sell");
    const long = list.find((leg) => leg.side === "buy");
    if (!short || !long || !long.expiry) return null;
    return {
      legs: { short_strike: short.strike, long_strike: long.strike, long_expiry: long.expiry },
      longExpiry: long.expiry,
    };
  }
  return null;
}
