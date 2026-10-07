import type { LoadableStructure, OptionKind, Strategy } from "../../types/options";
import type { Order } from "../../types/trading";
import { parseOcc } from "../../utils/occ";

interface Leg {
  kind: OptionKind;
  strike: number;
  expiry: string;
  side: "buy" | "sell";
  underlying: string;
}

const DAY_MS = 24 * 3600 * 1000;

function legsOf(order: Order): Order[] {
  return order.legs && order.legs.length > 0 ? order.legs : [order];
}

function endedAt(order: Order): number {
  return Date.parse(order.expired_at ?? order.submitted_at ?? "");
}

/** The packages worth offering again: opening orders that ran out at a
 * close in the last `days` days, newest first, one per set of contracts
 * (a re-sent order that expired again replaces the older one), and none
 * that is resting again right now. */
export function expiredToResend(closed: Order[], open: Order[], now: number, days = 4): Order[] {
  const key = (o: Order) =>
    legsOf(o)
      .map((l) => `${l.side}:${l.symbol}`)
      .sort()
      .join("|");
  const resting = new Set(open.map(key));
  const seen = new Set<string>();
  const out: Order[] = [];
  const recent = closed
    .filter((o) => o.status === "expired")
    .filter((o) => {
      const at = endedAt(o);
      return Number.isFinite(at) && now - at <= days * DAY_MS;
    })
    .filter((o) => !legsOf(o).some((l) => (l.position_intent ?? "").endsWith("_to_close")))
    .sort((a, b) => endedAt(b) - endedAt(a));
  for (const o of recent) {
    const k = key(o);
    if (resting.has(k) || seen.has(k)) continue;
    seen.add(k);
    out.push(o);
  }
  return out;
}

/** The order's contracts as a structure the ticket loads by strikes: an
 * iron condor, one of the four verticals, or a long call / put. Null for
 * any other shape -- those are rebuilt by hand. The limit is left out on
 * purpose: the ticket prices the structure at today's mid. */
export function structureFromOrder(order: Order): LoadableStructure | null {
  const legs: Leg[] = [];
  for (const raw of legsOf(order)) {
    const occ = raw.symbol ? parseOcc(raw.symbol) : null;
    const ratio = Number(raw.ratio_qty ?? 1);
    if (!occ || (raw.side !== "buy" && raw.side !== "sell") || ratio !== 1) return null;
    legs.push({ kind: occ.kind, strike: occ.strike, expiry: occ.expiry, side: raw.side, underlying: occ.underlying });
  }
  if (legs.length === 0 || new Set(legs.map((l) => l.expiry)).size > 1 || new Set(legs.map((l) => l.underlying)).size > 1) {
    return null;
  }
  const qty = Math.max(1, Math.round(Number(order.qty ?? 1)) || 1);
  const base = { underlying: legs[0].underlying, expiry: legs[0].expiry, qty };
  const of = (kind: OptionKind, side: "buy" | "sell") => legs.filter((l) => l.kind === kind && l.side === side);

  if (legs.length === 1 && legs[0].side === "buy") {
    const strategy: Strategy = legs[0].kind === "call" ? "long_call" : "long_put";
    return { strategy, ticket: { ...base, strategy, long_strike: legs[0].strike } };
  }
  if (legs.length === 2 && legs[0].kind === legs[1].kind && legs[0].side !== legs[1].side) {
    const kind = legs[0].kind;
    const long = of(kind, "buy")[0];
    const short = of(kind, "sell")[0];
    const strategy: Strategy =
      kind === "put" ? (short.strike > long.strike ? "bull_put" : "bear_put") : short.strike < long.strike ? "bear_call" : "bull_call";
    return { strategy, ticket: { ...base, strategy, long_strike: long.strike, short_strike: short.strike } };
  }
  if (legs.length === 4) {
    const [pl, ps, cs, cl] = [of("put", "buy"), of("put", "sell"), of("call", "sell"), of("call", "buy")];
    const single = [pl, ps, cs, cl].every((g) => g.length === 1);
    if (single && pl[0].strike < ps[0].strike && ps[0].strike < cs[0].strike && cs[0].strike < cl[0].strike) {
      return {
        strategy: "iron_condor",
        ticket: {
          ...base,
          strategy: "iron_condor",
          put_long_strike: pl[0].strike,
          put_short_strike: ps[0].strike,
          call_short_strike: cs[0].strike,
          call_long_strike: cl[0].strike,
        },
      };
    }
  }
  return null;
}
