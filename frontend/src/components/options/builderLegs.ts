/** The builder's legs: a plain list, not one of legPicker's named shapes.
 *
 * Every other strategy in the ticket knows its own arrangement -- a
 * vertical is a long and a short of one kind, a condor is four legs in a
 * fixed order -- and legPicker keeps that knowledge so a click on the
 * chain can mean "move the short leg here". The builder has no
 * arrangement to know: legs are added, flipped, weighted and removed one
 * at a time, and what the package then is (a credit spread, a ratio, a
 * naked short) is read off the result by the backend.
 */

import type { ChainResponse, OptionKind, SpreadTicketRequest } from "../../types/options";
import type { LegSelection } from "./ChainTable";
import { legKey } from "./ChainTable";
import { quoted, type LegHandle } from "./legPicker";

export const MAX_BUILDER_LEGS = 4;
export const MAX_BUILDER_RATIO = 10;

export interface BuilderLeg {
  /** Stable across edits, so a row keeps its identity while its strike
   * changes (and so React keys do not collide on two legs of one strike). */
  id: string;
  kind: OptionKind;
  strike: number;
  side: "buy" | "sell";
  ratio: number;
  /** YYYY-MM-DD; undefined = the ticket's own expiry. */
  expiry?: string;
}

let counter = 0;

function nextId(): string {
  counter += 1;
  return `leg-${counter}`;
}

export function makeLeg(kind: OptionKind, strike: number, side: "buy" | "sell" = "buy", expiry?: string): BuilderLeg {
  return { id: nextId(), kind, strike, side, ratio: 1, expiry };
}

/** The strike nearest the money that actually has a quote -- where a new
 * leg starts when nothing says otherwise. */
export function atmStrike(chain: ChainResponse, kind: OptionKind): number | null {
  const rows = quoted(chain.rows, kind);
  if (rows.length === 0) return null;
  return rows.reduce((best, row) => (Math.abs(row.strike - chain.spot) < Math.abs(best.strike - chain.spot) ? row : best))
    .strike;
}

/** What a click on the chain does while the builder is showing: the first
 * click buys that contract, the second sells it, the third takes it off
 * again. Three states on one cell rather than a modifier key, because the
 * cell has to say what it did and there is nowhere to show a hint. */
export function toggleLeg(legs: BuilderLeg[], kind: OptionKind, strike: number, expiry?: string): BuilderLeg[] {
  const at = legs.findIndex((leg) => leg.kind === kind && leg.strike === strike && leg.expiry === expiry);
  if (at === -1) {
    if (legs.length >= MAX_BUILDER_LEGS) return legs;
    return [...legs, makeLeg(kind, strike, "buy", expiry)];
  }
  const leg = legs[at];
  if (leg.side === "buy") {
    const next = [...legs];
    next[at] = { ...leg, side: "sell" };
    return next;
  }
  return legs.filter((_, i) => i !== at);
}

export function updateLeg(legs: BuilderLeg[], id: string, patch: Partial<Omit<BuilderLeg, "id">>): BuilderLeg[] {
  return legs.map((leg) => (leg.id === id ? { ...leg, ...patch } : leg));
}

export function removeLeg(legs: BuilderLeg[], id: string): BuilderLeg[] {
  return legs.filter((leg) => leg.id !== id);
}

/** Moves a leg to another contract, the way a drag across the chain will:
 * refuses to land on one the package already holds (two legs of the same
 * contract are one leg with a ratio, which the backend rejects). */
export function moveBuilderLeg(legs: BuilderLeg[], id: string, kind: OptionKind, strike: number, expiry?: string): BuilderLeg[] {
  if (legs.some((leg) => leg.id !== id && leg.kind === kind && leg.strike === strike && leg.expiry === expiry)) {
    return legs;
  }
  return updateLeg(legs, id, { kind, strike, expiry });
}

/** The next leg an "Add leg" click makes: at the money, bought, of the
 * kind the package is already made of (or a call to start with). */
export function nextLeg(legs: BuilderLeg[], chain: ChainResponse | null): BuilderLeg | null {
  if (!chain || legs.length >= MAX_BUILDER_LEGS) return null;
  const kind: OptionKind = legs.length > 0 ? legs[legs.length - 1].kind : "call";
  const held = new Set(legs.filter((leg) => leg.kind === kind).map((leg) => leg.strike));
  const rows = quoted(chain.rows, kind).filter((row) => !held.has(row.strike));
  if (rows.length === 0) return null;
  const at = rows.reduce((best, row) => (Math.abs(row.strike - chain.spot) < Math.abs(best.strike - chain.spot) ? row : best));
  // A second leg of the same kind starts on the other side of the first,
  // which is the spread someone is usually after.
  const previous = legs.filter((leg) => leg.kind === kind).map((leg) => leg.strike);
  if (previous.length > 0) {
    const anchor = previous[previous.length - 1];
    const outward = rows.filter((row) => (anchor >= chain.spot ? row.strike > anchor : row.strike < anchor));
    if (outward.length > 0) {
      const nearest = outward.reduce((best, row) => (Math.abs(row.strike - anchor) < Math.abs(best.strike - anchor) ? row : best));
      return makeLeg(kind, nearest.strike, legs[legs.length - 1].side === "buy" ? "sell" : "buy");
    }
  }
  return makeLeg(kind, at.strike, "buy");
}

/** Which chain cells to mark, and how. */
export function builderSelection(legs: BuilderLeg[]): LegSelection {
  const selection: LegSelection = new Map();
  for (const leg of legs) {
    selection.set(legKey(leg.kind, leg.strike), leg.side === "buy" ? "long" : "short");
  }
  return selection;
}

/** The strikes the chart draws while the builder is showing. */
export function builderLevels(legs: BuilderLeg[]): { label: string; price: number; role: "long" | "short" }[] {
  return legs.map((leg) => ({
    label: `${leg.side === "buy" ? "Long" : "Short"} ${leg.strike}${leg.kind === "put" ? "P" : "C"}`,
    price: leg.strike,
    role: leg.side === "buy" ? "long" : "short",
  }));
}

/** A short label for the ticket's header: +2 755C / -1 760C. */
export function builderLabel(legs: BuilderLeg[]): string {
  return legs
    .map((leg) => `${leg.side === "buy" ? "+" : "−"}${leg.ratio > 1 ? leg.ratio : ""} ${leg.strike}${leg.kind === "put" ? "P" : "C"}`)
    .join(" / ");
}

export function builderTicket(
  symbol: string,
  expiry: string,
  qty: number,
  legs: BuilderLeg[],
  withShares = false,
): SpreadTicketRequest {
  return {
    underlying: symbol,
    strategy: "custom",
    expiry,
    qty,
    ...(withShares ? { with_shares: true } : {}),
    legs: legs.map((leg) => ({
      kind: leg.kind,
      strike: leg.strike,
      side: leg.side,
      ratio: leg.ratio,
      ...(leg.expiry && leg.expiry !== expiry ? { expiry: leg.expiry } : {}),
    })),
  };
}

/** The shapes the builder can start from -- Natenberg's ratio spread,
 * Christmas tree and time butterfly (ch. 11), and the two share hedges
 * (ch. 17). Each is a starting arrangement; every leg stays editable. */
export type BuilderTemplate =
  | "call_ratio"
  | "put_ratio"
  | "christmas_tree"
  | "time_butterfly"
  | "protective_put"
  | "collar";

export const BUILDER_TEMPLATES: { key: BuilderTemplate; label: string; title: string }[] = [
  {
    key: "call_ratio",
    label: "Call ratio 1×2",
    title:
      "Buy one call near the money, sell two further out (about 25 delta). Little or no debit, profits in a modest rise, and the second short call is uncovered above: Alpaca takes no uncovered short, so this is a Simulation shape.",
  },
  {
    key: "put_ratio",
    label: "Put ratio 1×2",
    title: "The same below: buy one put near the money, sell two further out. Uncovered on the way down; Simulation only.",
  },
  {
    key: "christmas_tree",
    label: "Christmas tree",
    title:
      "Buy one call near the money, sell one at about 35 and one at about 20 delta: a ratio spread with its short strikes spread out. Short volatility, profits in a measured rise; one short is uncovered, Simulation only.",
  },
  {
    key: "time_butterfly",
    label: "Time butterfly",
    title:
      "At one strike near the money: buy this expiry, sell two of the next, buy one of the one after -- a short calendar and a long one together. A bet on how implied volatility is spread over time; the middle short outlives the near long, so Alpaca counts one short as uncovered: Simulation only.",
  },
  {
    key: "protective_put",
    label: "Protective put",
    title:
      "Buy a put about 30 delta below the price against shares you hold, one contract per 100: the shares' loss stops at the strike, for the premium. The risk chart carries the shares.",
  },
  {
    key: "collar",
    label: "Collar",
    title:
      "Against shares you hold: buy a put about 25 delta below, sell a call about 25 delta above. The call pays for most of the put; the shares are protected below the put and given up above the call. At Alpaca it is sent leg by leg, the put first.",
  },
];

/** The quoted strike whose |delta| is nearest `target`, else the one about
 * as far from spot as that delta would suggest (`fallbackPct`). */
function strikeNearDelta(chain: ChainResponse, kind: OptionKind, target: number, fallbackPct: number): number | null {
  const rows = quoted(chain.rows, kind);
  if (rows.length === 0) return null;
  const withDelta = rows.filter((r) => (kind === "put" ? r.put?.delta : r.call?.delta) != null);
  if (withDelta.length) {
    const d = (r: (typeof rows)[number]) => Math.abs((kind === "put" ? r.put!.delta! : r.call!.delta!) as number);
    return withDelta.reduce((best, r) => (Math.abs(d(r) - target) < Math.abs(d(best) - target) ? r : best)).strike;
  }
  const aim = kind === "call" ? chain.spot * (1 + fallbackPct) : chain.spot * (1 - fallbackPct);
  return rows.reduce((best, r) => (Math.abs(r.strike - aim) < Math.abs(best.strike - aim) ? r : best)).strike;
}

/** The legs a template starts from on `chain`, and whether it is written
 * against shares. Null when the chain cannot place it (no quotes; for the
 * time butterfly, fewer than two later expiries). `laterExpiries` are the
 * listed expiries after the ticket's, nearest first. */
export function templateLegs(
  template: BuilderTemplate,
  chain: ChainResponse,
  laterExpiries: string[],
): { legs: BuilderLeg[]; withShares: boolean } | null {
  const near = (kind: OptionKind, delta: number, pct: number) => strikeNearDelta(chain, kind, delta, pct);
  const withRatio = (leg: BuilderLeg, ratio: number): BuilderLeg => ({ ...leg, ratio });
  switch (template) {
    case "call_ratio":
    case "put_ratio": {
      const kind: OptionKind = template === "call_ratio" ? "call" : "put";
      const long = near(kind, 0.5, 0);
      const short = near(kind, 0.25, 0.05);
      if (long == null || short == null || long === short) return null;
      return { legs: [makeLeg(kind, long, "buy"), withRatio(makeLeg(kind, short, "sell"), 2)], withShares: false };
    }
    case "christmas_tree": {
      const a = near("call", 0.5, 0);
      const b = near("call", 0.35, 0.03);
      const c = near("call", 0.2, 0.06);
      if (a == null || b == null || c == null || new Set([a, b, c]).size < 3) return null;
      return { legs: [makeLeg("call", a, "buy"), makeLeg("call", b, "sell"), makeLeg("call", c, "sell")], withShares: false };
    }
    case "time_butterfly": {
      const strike = atmStrike(chain, "call");
      if (strike == null || laterExpiries.length < 2) return null;
      return {
        legs: [
          makeLeg("call", strike, "buy"),
          withRatio(makeLeg("call", strike, "sell", laterExpiries[0]), 2),
          makeLeg("call", strike, "buy", laterExpiries[1]),
        ],
        withShares: false,
      };
    }
    case "protective_put": {
      const put = near("put", 0.3, 0.04);
      if (put == null) return null;
      return { legs: [makeLeg("put", put, "buy")], withShares: true };
    }
    case "collar": {
      const put = near("put", 0.25, 0.05);
      const call = near("call", 0.25, 0.05);
      if (put == null || call == null) return null;
      return { legs: [makeLeg("put", put, "buy"), makeLeg("call", call, "sell")], withShares: true };
    }
  }
}

/** What the level badge asks, mirroring the backend's level_for_legs.
 * Three is the ceiling -- Alpaca has no fourth level, and a package with
 * an uncovered short is not a level question at all: it takes no such
 * position at any level (see nakedLegs and the ticket's own guard). */
export function levelForBuilder(legs: BuilderLeg[]): number {
  if (legs.length === 0) return 2;
  return legs.some((leg) => leg.side === "sell") ? 3 : 2;
}

export function nakedLegs(legs: BuilderLeg[], ticketExpiry: string): BuilderLeg[] {
  const bare: BuilderLeg[] = [];
  for (const kind of ["call", "put"] as const) {
    const shorts = legs
      .filter((leg) => leg.kind === kind && leg.side === "sell")
      .sort((a, b) => (a.expiry ?? ticketExpiry).localeCompare(b.expiry ?? ticketExpiry));
    const longs = legs.filter((leg) => leg.kind === kind && leg.side === "buy");
    const cover = new Map(longs.map((leg) => [leg.id, leg.ratio]));
    for (const short of shorts) {
      let need = short.ratio;
      for (const long of longs) {
        const left = cover.get(long.id) ?? 0;
        if (left <= 0) continue;
        if ((long.expiry ?? ticketExpiry) < (short.expiry ?? ticketExpiry)) continue;
        const taken = Math.min(need, left);
        cover.set(long.id, left - taken);
        need -= taken;
        if (need === 0) break;
      }
      if (need > 0) bare.push(short);
    }
  }
  return bare;
}

/** The rail's handles for a built package: one per leg, dragged and
 * nudged exactly like a named shape's, with the leg's own id so the move
 * comes back addressed to it. */
export function builderHandles(legs: BuilderLeg[]): LegHandle[] {
  return legs.map((leg) => ({
    id: leg.id,
    kind: leg.kind,
    strike: leg.strike,
    role: leg.side === "buy" ? "long" : "short",
    label: `${leg.side === "buy" ? "Long" : "Short"} ${leg.kind === "put" ? "put" : "call"}`,
  }));
}

/** Every leg moved `deltaSteps` listed strikes of its own kind, the way
 * shift-dragging the rail moves a named shape: offsets kept, and the
 * whole thing stands still rather than deforming when one leg would run
 * off the end of the board. */
export function shiftBuilderLegs(legs: BuilderLeg[], chain: ChainResponse, deltaSteps: number): BuilderLeg[] {
  if (deltaSteps === 0) return legs;
  const moved: BuilderLeg[] = [];
  for (const leg of legs) {
    const strikes = quoted(chain.rows, leg.kind).map((row) => row.strike);
    const at = strikes.indexOf(leg.strike);
    const to = at + deltaSteps;
    if (at === -1 || to < 0 || to >= strikes.length) return legs;
    moved.push({ ...leg, strike: strikes[to] });
  }
  // Two legs landing on one contract is not a package the backend takes.
  const seen = new Set(moved.map((leg) => `${leg.kind}:${leg.strike}:${leg.expiry ?? ""}`));
  return seen.size === moved.length ? moved : legs;
}
