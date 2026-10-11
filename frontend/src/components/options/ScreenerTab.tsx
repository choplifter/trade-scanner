import { useEffect, useState } from "react";
import { BarchartImportPanel } from "./BarchartImportPanel";
import { NATENBERG, withBook } from "./bookRefs";

import { OrderRejectedError } from "../../api/http";
import { latestScreen, screenUnderlyings } from "../../api/options";
import { getStored, setStored } from "../../api/prefs";
import { useWatchlist } from "../../hooks/useWatchlist";
import type {
  LoadableStructure,
  OptimizerOutlook,
  ScreenOutcome,
  ScreenResponse,
  ScreenRow,
  ScreenShortLeg,
  ScreenStrategy,
  ScreenCandidate,
  OptionKind,
  ScreenVertical,
  Strategy,
} from "../../types/options";
import { formatMoney, formatPrice } from "../../utils/format";
import { formatExpiry } from "../../utils/occ";
import { ScreenerHelp } from "./ScreenerHelp";
import { CHEAP_IV_RATIO, CONE_TITLE, ivTone, RICH_IV_RATIO, ratioTone, sideTone, toneClass } from "./eventMarks";
import { requestOptimizer } from "./optimizerIntent";
import { withGloss } from "./glossary";
import { requestTicket } from "./ticketIntent";

interface ScreenerTabProps {
  /** Clicking a row loads that symbol into the widget (and the chart). */
  onSelectSymbol?: (symbol: string) => void;
}

/** The screens that sell premium -- rich IV is good for them; the rest
 * (debit spread, long option, calendar) pay for it, the ticket's rule for a
 * debit. */
const SELLING_SCREENS = new Set<ScreenStrategy>(["cash_secured_put", "covered_call", "credit_spread", "iron_condor"]);

const STRATEGIES: { key: ScreenStrategy; label: string; title: string }[] = [
  {
    key: "cash_secured_put",
    label: "Cash-sec. put",
    title: "Wants implied volatility rich against realised, deep open interest and tight quotes at a 0.10-0.20 delta put.",
  },
  {
    key: "covered_call",
    label: "Covered call",
    title: "The same chain qualities, read off the call side.",
  },
  {
    key: "credit_spread",
    label: "Credit spread",
    title: "A vertical written for a credit -- a bull put on the puts or a bear call on the calls, chosen beside it: the short at the delta band, a further strike bought as the wing, with enough credit against the width it risks. The chain qualities as for the cash-secured put.",
  },
  {
    key: "iron_condor",
    label: "Iron condor",
    title: "A wing on both sides, and both verticals paying enough against their width.",
  },
  {
    key: "debit_spread",
    label: "Debit spread",
    title: "A vertical bought for a debit -- a bear put or a bull call, chosen beside it: the long leg at the delta band, a further strike sold against it. Wants implied volatility cheap against realised.",
  },
  // Long option and calendar are screened by the backend but judge only the
  // chain -- no structure, no expectation, nothing to load -- so they stay
  // off the bar until they name a structure like the rest.
];

/**
 * The structure the row's own numbers already describe, ready for the
 * ticket -- the screener found the expiry and the strikes, so sending the
 * reader back to the chain to re-pick them by hand would be busywork.
 *
 * Null where the row does not name a shape: a long option has no strike
 * to build from (the screen judged the chain, not a direction), and a
 * calendar's second leg is in another expiry the row carries only as a
 * date. Neither is offered on the bar any more (see STRATEGIES).
 */
export function structureOf(row: ScreenRow, strategy: ScreenStrategy): LoadableStructure | null {
  if (!row.expiry) return null;
  const base = { underlying: row.symbol, expiry: row.expiry, qty: 1 };
  const put = row.short_put;
  const call = row.short_call;
  if (strategy === "cash_secured_put" && put) {
    return { strategy: "cash_secured_put" as Strategy, ticket: { ...base, strategy: "cash_secured_put" as Strategy, short_strike: put.strike } };
  }
  if (strategy === "covered_call" && call) {
    return { strategy: "covered_call" as Strategy, ticket: { ...base, strategy: "covered_call" as Strategy, short_strike: call.strike } };
  }
  if (strategy === "credit_spread" && (row.put_spread || row.call_spread)) {
    // A put vertical written below the money is a bull put, a call
    // vertical written above it a bear call.
    const pair = row.put_spread ?? row.call_spread!;
    const shape = (row.put_spread ? "bull_put" : "bear_call") as Strategy;
    return {
      strategy: shape,
      ticket: { ...base, strategy: shape, long_strike: pair.long_strike, short_strike: pair.short_strike },
    };
  }
  if (strategy === "debit_spread" && (row.put_spread || row.call_spread)) {
    // Bought: the leg at the delta band is the long one, the wing beyond it
    // is sold -- a bear put on the puts, a bull call on the calls.
    const pair = row.put_spread ?? row.call_spread!;
    const shape = (row.put_spread ? "bear_put" : "bull_call") as Strategy;
    return {
      strategy: shape,
      ticket: { ...base, strategy: shape, long_strike: pair.short_strike, short_strike: pair.long_strike },
    };
  }
  if (strategy === "iron_condor" && row.put_spread && row.call_spread) {
    return {
      strategy: "iron_condor" as Strategy,
      ticket: {
        ...base,
        strategy: "iron_condor" as Strategy,
        put_long_strike: row.put_spread.long_strike,
        put_short_strike: row.put_spread.short_strike,
        call_short_strike: row.call_spread.short_strike,
        call_long_strike: row.call_spread.long_strike,
      },
    };
  }
  return null;
}

/** The Optimizer question this screen's strategy asks: which family to
 * rank, and the view that sets the target. The screened expiry travels
 * with it, so the tab opens on the same horizon the row was judged at
 * rather than on its own default. */
const OPTIMIZER_INTENT: Record<ScreenStrategy, { outlook: OptimizerOutlook; strategies: Strategy[]; income?: boolean }> = {
  // Income structures put up the position itself -- the strike in cash, the
  // hundred shares -- so the Optimizer's spread-sized default budget would
  // drop every candidate as over budget and answer with an empty list.
  cash_secured_put: { outlook: "bullish", strategies: ["cash_secured_put"], income: true },
  covered_call: { outlook: "neutral", strategies: ["covered_call"], income: true },
  // The screen read the put side, so the vertical it ranks is the bull put.
  credit_spread: { outlook: "bullish", strategies: ["bull_put"] },
  iron_condor: { outlook: "neutral", strategies: ["iron_condor"] },
  debit_spread: { outlook: "bullish", strategies: ["bull_call"] },
  long_option: { outlook: "bullish", strategies: ["long_call"] },
  calendar: { outlook: "neutral", strategies: ["calendar"] },
};

function reasonFor(row: ScreenRow): string {
  const parts = [`${row.passed}/${row.scored} criteria`];
  if (row.iv_rv_ratio != null) parts.push(`IV ${row.iv_rv_ratio.toFixed(2)}x realised${row.ref_dte != null ? ` (on the ${row.ref_dte}-day expiry)` : ""}`);
  if (row.iv_rank != null) parts.push(`IV rank ${row.iv_rank.toFixed(0)} %`);
  return `screen: ${parts.join(" · ")}`;
}

/** A typed bound, or the default when the field is empty or nonsense --
 * a half-typed "0." must not send NaN and be refused by the validator. */
export function numeric(text: string, fallback: number, allowZero = false): number {
  const value = Number(text);
  if (text.trim() === "" || !Number.isFinite(value)) return fallback;
  return value > 0 || (allowZero && value === 0) ? value : fallback;
}

/** How old a stored run is, in the words a trader uses about a table. */
function ageOf(iso: string): string {
  const minutes = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  return hours < 24 ? `${hours} h ago` : `${Math.round(hours / 24)} d ago`;
}

function pct(value: number | null | undefined, digits = 0): string {
  return value == null ? "—" : `${(value * 100).toFixed(digits)} %`;
}

/** The widest quote anywhere in the structure, as a share of its own mid.
 *
 * A package fills at the price of its worst leg, so this one number
 * answers "can this be traded at all" -- the question the help panel says
 * to settle before reading anything else, and the one the table could not
 * be ordered by. Up to four values were spread across two cells and the
 * reader had to find the largest by eye.
 */
function worstLeg(row: ScreenRow): { fraction: number; side: "P" | "C"; what: string } | null {
  const legs: { fraction: number | null | undefined; side: "P" | "C"; what: string }[] = [
    { fraction: row.short_put?.spread_fraction, side: "P", what: `short put ${row.short_put?.strike ?? ""}`.trim() },
    { fraction: row.put_spread?.wing_spread_fraction, side: "P", what: `put wing ${row.put_spread?.long_strike ?? ""}`.trim() },
    { fraction: row.short_call?.spread_fraction, side: "C", what: `short call ${row.short_call?.strike ?? ""}`.trim() },
    { fraction: row.call_spread?.wing_spread_fraction, side: "C", what: `call wing ${row.call_spread?.long_strike ?? ""}`.trim() },
  ];
  const known = legs.filter((l): l is { fraction: number; side: "P" | "C"; what: string } => l.fraction != null);
  if (!known.length) return null;
  return known.reduce((a, b) => (b.fraction > a.fraction ? b : a));
}

function worstQuote(row: ScreenRow): number | null {
  return worstLeg(row)?.fraction ?? null;
}

/** What each sortable column reads off a row. The server ranks by criteria
 * passed, which is the right default and the wrong lens for several real
 * questions: the highest expectancy on one side of a condor sat eight rows
 * down behind structures that merely failed fewer tests, and there was no
 * way to bring it up. Null sorts last whichever direction is chosen -- a
 * row that could not be measured is not the best or the worst of anything.
 */
const SORTS: Record<string, (r: ScreenRow) => number | string | null> = {
  Symbol: (r) => r.symbol,
  Passed: (r) => r.passed,
  "Worst leg": worstQuote,
  IV: (r) => r.atm_iv,
  RV: (r) => r.realised_vol,
  "IV/RV": (r) => r.iv_rv_ratio,
  "IV rank": (r) => r.iv_rank,
  Cone: (r) => r.cone_pct,
  OI: (r) => r.open_interest,
  "Vol/OI": (r) => r.volume_oi_ratio,
  Expiry: (r) => r.dte,
  "Put side": (r) => r.put_outcome?.expected_value ?? null,
  "Call side": (r) => r.call_outcome?.expected_value ?? null,
  "Bull put": (r) => r.put_outcome?.expected_value ?? null,
  "Bear put": (r) => r.put_outcome?.expected_value ?? null,
  "Bear call": (r) => r.call_outcome?.expected_value ?? null,
  "Bull call": (r) => r.call_outcome?.expected_value ?? null,
  "Short put": (r) => r.short_put?.delta ?? null,
  "Short call": (r) => r.short_call?.delta ?? null,
  "Loss : Gain": (r) => r.outcome?.risk_reward ?? null,
  // Reports inside the expiry first, the soonest at the top.
  Earnings: (r) => (r.earnings_date && earningsInside(r).inside ? Date.parse(r.earnings_date) : null),
  "Loss prob": (r) => r.outcome?.loss_probability ?? null,
  EV: (r) => r.outcome?.expected_value ?? null,
  "BE vol": (r) => r.outcome?.breakeven_vol ?? null,
  "To BE": (r) => (r.outcome?.to_breakeven_sigma != null ? Math.abs(r.outcome.to_breakeven_sigma) : null),
  "Risk %": (r) => r.risk_share,
};

/** The structure's breakeven volatility and its margin over the realised
 * one -- a credit wants the stock to move less than the price implies, a
 * debit more. */
function BreakevenVolCell({ row, buyer }: { row: ScreenRow; buyer: boolean }) {
  const be = row.outcome?.breakeven_vol ?? null;
  if (be == null) return <td>—</td>;
  // Against the volatility expected over the expiry's life; the bare
  // 20-session reading when there is no year of closes to blend with.
  const expected = row.forecast_vol ?? row.realised_vol;
  const margin = expected == null ? null : (buyer ? expected - be : be - expected) * 100;
  return (
    <td className={margin == null ? undefined : margin >= 0 ? "delta-up" : "delta-down"}>
      {(be * 100).toFixed(1)}%{margin == null ? "" : ` (${margin >= 0 ? "+" : ""}${margin.toFixed(1)})`}
    </td>
  );
}

/** The sides a screen's rows carry, and what each column is called: both
 * for a condor ("Put side", "Call side"), one named after the structure
 * otherwise. An older stored run carries no `sides`: read off the strategy. */
function sideColumns(result: ScreenResponse): { kind: OptionKind; label: string }[] {
  const strategy = result.strategy;
  const sides: OptionKind[] =
    result.sides ??
    (strategy === "cash_secured_put" || strategy === "credit_spread" || strategy === "debit_spread"
      ? ["put"]
      : strategy === "covered_call"
        ? ["call"]
        : ["put", "call"]);
  if (sides.length === 2) return [{ kind: "put", label: "Put side" }, { kind: "call", label: "Call side" }];
  const kind = sides[0];
  const label =
    strategy === "credit_spread" || strategy === "debit_spread"
      ? VERTICAL_LABELS[strategy][kind]
      : kind === "put"
        ? "Short put"
        : "Short call";
  return [{ kind, label }];
}

/** Limits on a structure's own numbers, applied to the rows on screen --
 * no new run needed, so they work on a stored run too. Each is a string as
 * typed; blank means no limit. */
interface ResultFilters {
  /** Loss probability at most, in percent. */
  maxLossProb: string;
  /** Maximum profit per contract at least, in dollars. */
  minProfit: string;
  /** Maximum loss per contract at most, in dollars (a positive number). */
  maxLoss: string;
  /** Loss : gain at most, e.g. 5 for 5 : 1. */
  maxLossGain: string;
  /** Expected value per contract at least, in dollars. */
  minEv: string;
  /** Nearest breakeven at least this many sigmas from the price. */
  minToBe: string;
  /** Breakeven vol at least this many points past the forecast, read for
   * the structure's side -- where a seller's edge comes from. */
  minMargin: string;
  /** Implied over realised volatility at least. */
  minIvRv: string;
  /** "1": only rows whose earnings criterion passed -- under "early", a
   * report late in the position's life is otherwise still listed. */
  earningsOk: string;
}

const NO_FILTERS: ResultFilters = {
  maxLossProb: "",
  minProfit: "",
  maxLoss: "",
  maxLossGain: "",
  minEv: "",
  minToBe: "",
  minMargin: "",
  minIvRv: "",
  earningsOk: "",
};

/** What a row knows that its outcome does not: the forecast its margin is
 * read against, its IV/RV, and which side of volatility it is on. */
export interface FilterContext {
  forecast: number | null;
  ivRv: number | null;
  buyer: boolean;
  /** The breakeven's distance in sigmas when the outcome does not carry it
   * (a run stored before it did): read off the strikes, credit and IV. */
  toBeSigma?: number | null;
  /** The row's earnings criterion: false when it failed, true when it
   * passed, null when not judged or no date is known (an ETF). */
  earningsPassed?: boolean | null;
}

function limit(text: string): number | null {
  if (text.trim() === "") return null;
  const n = Number(text.replace(",", "."));
  return Number.isFinite(n) ? n : null;
}

/** Whether a structure's outcome meets every limit set. A number the
 * outcome does not carry fails a limit on it: "unknown" is not "within". */
export function passesFilters(
  o: ScreenOutcome | null | undefined,
  f: ResultFilters,
  ctx: FilterContext = { forecast: null, ivRv: null, buyer: false },
): boolean {
  const lossProb = limit(f.maxLossProb ?? "");
  const profit = limit(f.minProfit ?? "");
  const loss = limit(f.maxLoss ?? "");
  const ratio = limit(f.maxLossGain ?? "");
  const ev = limit(f.minEv ?? "");
  const toBe = limit(f.minToBe ?? "");
  const margin = limit(f.minMargin ?? "");
  const ivRv = limit(f.minIvRv ?? "");
  const earningsOk = (f.earningsOk ?? "") === "1";
  if (!earningsOk && [lossProb, profit, loss, ratio, ev, toBe, margin, ivRv].every((v) => v == null)) return true;
  // A row whose earnings rule failed: under "early", a report late in the
  // position's life. Unknown (no date, not judged) is not a failure here --
  // that is most often an ETF, which reports nothing.
  if (earningsOk && ctx.earningsPassed === false) return false;
  if ([lossProb, profit, loss, ratio, ev, toBe, margin, ivRv].every((v) => v == null)) return true;
  if (ivRv != null && !(ctx.ivRv != null && ctx.ivRv >= ivRv)) return false;
  if (!o) return false;
  if (margin != null) {
    if (o.breakeven_vol == null || ctx.forecast == null) return false;
    const gap = (o.breakeven_vol - ctx.forecast) * 100;
    if (!((ctx.buyer ? -gap : gap) >= margin)) return false;
  }
  if (lossProb != null && !(o.loss_probability * 100 <= lossProb)) return false;
  if (profit != null && !(o.max_profit != null && o.max_profit >= profit)) return false;
  if (loss != null && !(o.max_loss != null && Math.abs(o.max_loss) <= Math.abs(loss))) return false;
  if (ratio != null && !(o.risk_reward != null && o.risk_reward <= ratio)) return false;
  if (ev != null && !(o.expected_value >= ev)) return false;
  const sigmas = o.to_breakeven_sigma ?? ctx.toBeSigma ?? null;
  if (toBe != null && !(sigmas != null && Math.abs(sigmas) >= toBe)) return false;
  return true;
}

function activeFilterCount(f: ResultFilters): number {
  return Object.values(f).filter((v) => (v ?? "").trim() !== "").length;
}

/** "bull put spreads", "iron condors" -- what the run button says it scans for. */
function structureName(strategy: ScreenStrategy, side: OptionKind): string {
  if (strategy === "credit_spread") return side === "put" ? "bull put spreads" : "bear call spreads";
  if (strategy === "debit_spread") return side === "put" ? "bear put spreads" : "bull call spreads";
  if (strategy === "iron_condor") return "iron condors";
  if (strategy === "cash_secured_put") return "cash-secured puts";
  if (strategy === "covered_call") return "covered calls";
  return strategy.replace("_", " ");
}

function filtersActive(f: ResultFilters): boolean {
  return Object.values(f).some((v) => (v ?? "").trim() !== "");
}

/** The nearest breakeven's distance in sigmas from the row's own legs, for
 * runs stored before the outcome carried it: short strike less (put) or
 * plus (call) the credit, against the ATM IV over the days left. */
function toBeSigmaOf(row: ScreenRow): number | null {
  if (!row.spot || !row.atm_iv || !row.dte || row.dte <= 0) return null;
  const sigma = row.atm_iv * Math.sqrt(row.dte / 365);
  const credit = (row.put_spread?.credit ?? 0) + (row.call_spread?.credit ?? 0);
  const bes: number[] = [];
  if (row.put_spread) bes.push(row.put_spread.short_strike - credit);
  else if (row.short_put?.mid != null) bes.push(row.short_put.strike - row.short_put.mid);
  if (row.call_spread) bes.push(row.call_spread.short_strike + credit);
  else if (row.short_call?.mid != null && !row.short_put) bes.push(row.short_call.strike + row.short_call.mid);
  const valid = bes.filter((b) => b > 0);
  if (!valid.length) return null;
  return Math.min(...valid.map((b) => Math.abs(Math.log(b / row.spot!) / sigma)));
}

function contextOf(row: ScreenRow, strategy: ScreenStrategy): FilterContext {
  return {
    forecast: row.forecast_vol ?? row.realised_vol,
    ivRv: row.iv_rv_ratio,
    buyer: strategy === "debit_spread",
    toBeSigma: row.outcome?.to_breakeven_sigma ?? toBeSigmaOf(row),
    earningsPassed: row.criteria.find((c) => c.key === "earnings")?.passed ?? null,
  };
}

const FILTER_LABELS: Record<keyof ResultFilters, string> = {
  maxLossProb: "Loss",
  minProfit: "Profit",
  maxLoss: "Max loss",
  maxLossGain: "Loss : Gain",
  minEv: "EV",
  minToBe: "To BE",
  minMargin: "BE margin",
  minIvRv: "IV/RV",
  earningsOk: "Earnings",
};

/** Rows that fail exactly one active limit, and which: what to loosen when
 * nothing passes. */
export function nearMisses(
  rows: ScreenRow[],
  filters: ResultFilters,
  strategy: ScreenStrategy,
): { symbol: string; label: string }[] {
  const active = (Object.keys(filters) as (keyof ResultFilters)[]).filter((k) => (filters[k] ?? "").trim() !== "");
  const out: { symbol: string; label: string }[] = [];
  for (const row of rows) {
    const ctx = contextOf(row, strategy);
    if (passesFilters(row.outcome, filters, ctx)) continue;
    const failed = active.filter((k) => !passesFilters(row.outcome, { ...NO_FILTERS, [k]: filters[k] }, ctx));
    if (failed.length === 1) out.push({ symbol: row.symbol, label: FILTER_LABELS[failed[0]] });
  }
  return out;
}

/** How many of `rows` each active limit removes on its own -- what turns
 * "no rows" into "the earnings rule and BE margin took them". */
function filterFunnel(rows: ScreenRow[], filters: ResultFilters, strategy: ScreenStrategy): { label: string; removed: number }[] {
  return (Object.keys(filters) as (keyof ResultFilters)[])
    .filter((key) => (filters[key] ?? "").trim() !== "")
    .map((key) => {
      const only: ResultFilters = { ...NO_FILTERS, [key]: filters[key] };
      const removed = rows.filter((r) => !passesFilters(r.outcome, only, contextOf(r, strategy))).length;
      return { label: FILTER_LABELS[key], removed };
    });
}

// --- presets -------------------------------------------------------------------

/** Everything a screen is set to, under a name: the strategy and its side,
 * the screen's own bounds, the pricing, and the limits on the results. */
export interface ScreenPreset {
  name: string;
  builtIn?: boolean;
  /** Why it is set the way it is (built-in presets). */
  note?: string;
  strategy: ScreenStrategy;
  side: OptionKind;
  dteMin: string;
  dteMax: string;
  deltaMin: string;
  deltaMax: string;
  minOi: string;
  wingPoints: string;
  maxSpread: string;
  earnings: "avoid" | "early" | "ignore";
  priceAt: "mid" | "natural";
  twoExpiries: boolean;
  filters: ResultFilters;
}

/** The presets the app ships with. The first is the one argued for in the
 * note: premium selling where the edge is measurable, the fill honest and
 * the event risk out of the window. */
export const BUILT_IN_PRESETS: ScreenPreset[] = [
  {
    name: "Bot gems · 30–45 DTE",
    builtIn: true,
    note:
      "Bull put spreads 30-45 days out, short at 12-20 delta: the stretch where theta pays well and gamma is still mild. " +
      "Priced at the natural, so the credit is one a limit fills at. No earnings inside the expiry (the rich IV before a " +
      "report pays for the print, not an edge). Quotes within 10 %, open interest 5,000+. Kept only where the premium is " +
      "rich against the stock's own movement (IV/RV 1.2+) and the breakeven vol sits 3+ points over the forecast -- the " +
      "measured edge -- with loss probability up to 25 %, loss : gain up to 10 (a cap on far-out credits worth a rounding " +
      "error; a 16-delta spread runs 6-9 : 1) and the breakeven at least 0.9 sigma away. EV is left open: under the " +
      "market's own odds a fairly priced spread is about zero by construction. In earnings season expect few or no rows: " +
      "most of the rich premium then is the coming report, which this preset declines to sell.",
    strategy: "credit_spread",
    side: "put",
    dteMin: "30",
    dteMax: "45",
    deltaMin: "0.12",
    deltaMax: "0.20",
    minOi: "5000",
    wingPoints: "",
    maxSpread: "10",
    earnings: "avoid",
    priceAt: "natural",
    twoExpiries: false,
    filters: {
      ...NO_FILTERS,
      maxLossProb: "25",
      maxLossGain: "10",
      minToBe: "0.9",
      minMargin: "3",
      minIvRv: "1.2",
      earningsOk: "1",
    },
  },
  {
    name: "Bot gems · 30–45 DTE · early earnings",
    builtIn: true,
    note:
      "The same bull put spreads, but a report in the first third of the position's life is allowed: the implied " +
      "volatility the report props up collapses within days, while weeks of decay follow on strikes chosen for the " +
      "whole period. A report later than that is taken out (Earnings ok). This sells the print as well as the premium, " +
      "so the strikes sit further out to survive the gap -- short at 10-16 delta, the breakeven at least 1.1 sigma away " +
      "-- and the margin asked for is larger, 5+ points over the forecast with IV/RV 1.3+, because the forecast is read " +
      "off ordinary days and knows nothing of the report's jump: part of that margin is the jump itself. Size it as the " +
      "riskier trade it is.",
    strategy: "credit_spread",
    side: "put",
    dteMin: "30",
    dteMax: "45",
    deltaMin: "0.10",
    deltaMax: "0.16",
    minOi: "5000",
    wingPoints: "",
    maxSpread: "10",
    earnings: "early",
    priceAt: "natural",
    twoExpiries: false,
    filters: {
      ...NO_FILTERS,
      maxLossProb: "20",
      maxLossGain: "12",
      minToBe: "1.1",
      minMargin: "5",
      minIvRv: "1.3",
      earningsOk: "1",
    },
  },
  {
    name: "Bot gems · 0DTE bull put",
    builtIn: true,
    note:
      "Bull put spreads expiring today, on the names with daily expiries -- run it on a watchlist of SPY, QQQ, IWM (the " +
      "universe has few dailies). The short sits far out, at 5-12 delta, with the breakeven at least 1.1 sigma of the hours " +
      "left away: on expiry day gamma is at its highest, and a short near the money turns in minutes. The wing shrinks " +
      "with the time left. Loss probability up to 12 %, loss : gain up to 15 (thin credits are the nature of a far 0DTE " +
      "short -- EV and margin say whether they pay), breakeven vol 2+ points over the forecast. IV/RV is left open: for a " +
      "same-day expiry it is read on the 30-60 day IV, not on today's. Natural pricing, quotes within 10 %. Not in the " +
      "screen, but part of the trade: enter after the open has settled (10:00 ET or later), skip FOMC and CPI days, prefer " +
      "a positive dealer-gamma regime (see GEX), and close before 15:30 ET (15:45 for SPY and QQQ) -- Alpaca liquidates " +
      "what it cannot cover from then.",
    strategy: "credit_spread",
    side: "put",
    dteMin: "0",
    dteMax: "0",
    deltaMin: "0.05",
    deltaMax: "0.12",
    minOi: "2000",
    wingPoints: "",
    maxSpread: "10",
    earnings: "avoid",
    priceAt: "natural",
    twoExpiries: false,
    filters: {
      ...NO_FILTERS,
      maxLossProb: "12",
      maxLossGain: "15",
      minToBe: "1.1",
      minMargin: "2",
      earningsOk: "1",
    },
  },
  {
    name: "Bot gems · 0DTE bear call",
    builtIn: true,
    note:
      "Bear call spreads expiring today, on the names with daily expiries -- run it on a watchlist of SPY, QQQ, IWM. The " +
      "call side of an index carries little skew premium, so at the bull put's 5-12 delta the credit is thin; the short " +
      "sits a little nearer, at 8-15 delta, with the breakeven still at least 1.0 sigma of the hours left away. Loss " +
      "probability up to 15 %, loss : gain up to 15, breakeven vol 2+ points over the forecast; IV/RV left open (read on " +
      "the 30-60 day IV). Natural pricing, quotes within 10 %; a spread with no credit at the natural is left out. Not " +
      "in the screen, but part of the trade: the market's upward drift and its squeezes work against it, so take it on a " +
      "day that is not trending up -- under the call wall, after a run-up stalls, in a positive dealer-gamma regime (see " +
      "GEX). Enter after 10:00 ET, skip FOMC and CPI days, close before 15:30 ET (15:45 for SPY and QQQ) -- Alpaca " +
      "liquidates what it cannot cover from then.",
    strategy: "credit_spread",
    side: "call",
    dteMin: "0",
    dteMax: "0",
    deltaMin: "0.08",
    deltaMax: "0.15",
    minOi: "2000",
    wingPoints: "",
    maxSpread: "10",
    earnings: "avoid",
    priceAt: "natural",
    twoExpiries: false,
    filters: {
      ...NO_FILTERS,
      maxLossProb: "15",
      maxLossGain: "15",
      minToBe: "1.0",
      minMargin: "2",
      earningsOk: "1",
    },
  },
  {
    name: "Bot gems · 0DTE iron condor",
    builtIn: true,
    note:
      "Iron condors expiring today, on the names with daily expiries -- run it on a watchlist of SPY, QQQ, IWM. Both " +
      "shorts far out, at 5-12 delta, the nearer breakeven at least 1.0 sigma of the hours left away: the bet is a quiet " +
      "rest of the day, and on expiry day gamma turns a short near the money in minutes. Two credits on one width at risk, " +
      "so the bars are a little looser than the bull put's on loss : gain (up to 12) and wider on loss probability (up to " +
      "20 %, both tails together). The call side brings little on an index -- the skew sits in the puts -- and a condor " +
      "with no call credit at the natural is left out. Breakeven vol 2+ points over the forecast, IV/RV left open (read " +
      "on the 30-60 day IV). Natural pricing, quotes within 10 %. Not in the screen, but part of the trade: enter after " +
      "10:00 ET, skip FOMC and CPI days and trend days, prefer a positive dealer-gamma regime (see GEX), close a side " +
      "that is tested rather than wait, and close before 15:30 ET (15:45 for SPY and QQQ) -- Alpaca liquidates what it " +
      "cannot cover from then.",
    strategy: "iron_condor",
    side: "put",
    dteMin: "0",
    dteMax: "0",
    deltaMin: "0.05",
    deltaMax: "0.12",
    minOi: "2000",
    wingPoints: "",
    maxSpread: "10",
    earnings: "avoid",
    priceAt: "natural",
    twoExpiries: false,
    filters: {
      ...NO_FILTERS,
      maxLossProb: "20",
      maxLossGain: "12",
      minToBe: "1.0",
      minMargin: "2",
      earningsOk: "1",
    },
  },
  {
    name: "Bot gems · Bear call 30–45 DTE",
    builtIn: true,
    note:
      "The bull put's mirror on the calls, for when you would not bet on a rise: a call sold above the price at 12-20 " +
      "delta, a further one bought. Calls carry less skew premium than puts, so the same margin is rarer -- and the " +
      "stock's usual upward drift works against the position. Same bars otherwise: natural pricing, no earnings inside, " +
      "IV/RV 1.2+, breakeven vol 3+ points over the forecast, loss probability up to 25 %, loss : gain up to 10, the " +
      "breakeven 0.9 sigma+ away.",
    strategy: "credit_spread",
    side: "call",
    dteMin: "30",
    dteMax: "45",
    deltaMin: "0.12",
    deltaMax: "0.20",
    minOi: "5000",
    wingPoints: "",
    maxSpread: "10",
    earnings: "avoid",
    priceAt: "natural",
    twoExpiries: false,
    filters: {
      ...NO_FILTERS,
      maxLossProb: "25",
      maxLossGain: "10",
      minToBe: "0.9",
      minMargin: "3",
      minIvRv: "1.2",
      earningsOk: "1",
    },
  },
  {
    name: "Bot gems · Iron condor 30–45 DTE",
    builtIn: true,
    note:
      "A bull put and a bear call together, both shorts at 12-20 delta: twice the credit on the same width at risk, so " +
      "loss : gain is lower (about 3-5 : 1) -- but two sides can be breached, so the loss probability runs higher " +
      "(about 25-35 %). It suits a stock expected to stay in its range, in a market with positive dealer gamma (see the " +
      "GEX regime). Natural pricing, no earnings inside, IV/RV 1.2+, breakeven vol 3+ points over the forecast, the " +
      "nearer breakeven 0.8 sigma+ away.",
    strategy: "iron_condor",
    side: "put",
    dteMin: "30",
    dteMax: "45",
    deltaMin: "0.12",
    deltaMax: "0.20",
    minOi: "5000",
    wingPoints: "",
    maxSpread: "10",
    earnings: "avoid",
    priceAt: "natural",
    twoExpiries: false,
    filters: {
      ...NO_FILTERS,
      maxLossProb: "38",
      maxLossGain: "6",
      minToBe: "0.8",
      minMargin: "3",
      minIvRv: "1.2",
      earningsOk: "1",
    },
  },
  {
    name: "Bot gems · ETF condor 30–45 DTE",
    builtIn: true,
    note:
      "Iron condors on quiet index and sector ETFs (SPY, QQQ, IWM, DIA, TLT, XLF, XLV …) -- run it on a watchlist of " +
      "them; the universe picks the most traded names, mostly single stocks. At 13-25 % implied volatility a 12-20 " +
      "delta short brings too little against the width, so the shorts sit nearer, at 15-25 delta, and the bars are fitted " +
      "to a calm market: loss probability up to 40 %, loss : gain up to 6, the nearer breakeven 0.7 sigma+ away, " +
      "breakeven vol 2+ points over the forecast, IV/RV 1.1+. ETFs report no earnings, but the rule stays on avoid for " +
      "the few single names a watchlist holds. Natural pricing, quotes within 10 %.",
    strategy: "iron_condor",
    side: "put",
    dteMin: "30",
    dteMax: "45",
    deltaMin: "0.15",
    deltaMax: "0.25",
    minOi: "5000",
    wingPoints: "",
    maxSpread: "10",
    earnings: "avoid",
    priceAt: "natural",
    twoExpiries: false,
    filters: {
      ...NO_FILTERS,
      maxLossProb: "40",
      maxLossGain: "6",
      minToBe: "0.7",
      minMargin: "2",
      minIvRv: "1.1",
      earningsOk: "1",
    },
  },
  {
    name: "Bot gems · Cash-secured put 30–45 DTE",
    builtIn: true,
    note:
      "A put sold at 15-25 delta with the strike's cash set aside -- paid to place a limit order below the market. Only " +
      "on a stock you would hold if assigned: the loss runs to the strike less the premium, so loss : gain is not a " +
      "useful bar here and is left open. Natural pricing, no earnings inside, IV/RV 1.2+, breakeven vol 3+ points over " +
      "the forecast, loss probability up to 25 %, the breakeven 0.8 sigma+ away.",
    strategy: "cash_secured_put",
    side: "put",
    dteMin: "30",
    dteMax: "45",
    deltaMin: "0.15",
    deltaMax: "0.25",
    minOi: "5000",
    wingPoints: "",
    maxSpread: "10",
    earnings: "avoid",
    priceAt: "natural",
    twoExpiries: false,
    filters: {
      ...NO_FILTERS,
      maxLossProb: "25",
      minToBe: "0.8",
      minMargin: "3",
      minIvRv: "1.2",
      earningsOk: "1",
    },
  },
  {
    name: "Bot gems · Covered call 30–45 DTE",
    builtIn: true,
    note:
      "A call sold at 15-25 delta against 100 shares held per contract: income on stock you own, the gain above the " +
      "strike given up. The premium is judged like any other sale -- IV/RV 1.1+ and breakeven vol 2+ points over the " +
      "forecast (a little looser, since the shares are held anyway) -- with natural pricing and no earnings inside, where " +
      "a gap up would call the shares away at the strike.",
    strategy: "covered_call",
    side: "call",
    dteMin: "30",
    dteMax: "45",
    deltaMin: "0.15",
    deltaMax: "0.25",
    minOi: "5000",
    wingPoints: "",
    maxSpread: "10",
    earnings: "avoid",
    priceAt: "natural",
    twoExpiries: false,
    filters: {
      ...NO_FILTERS,
      minMargin: "2",
      minIvRv: "1.1",
      earningsOk: "1",
    },
  },
  {
    name: "Bot gems · Bull call (cheap vol) 30–45 DTE",
    builtIn: true,
    note:
      "The buyer's side: a call bought at 35-50 delta and one sold further up, for when implied volatility is cheap -- " +
      "the forecast at least 3 points over the breakeven vol (BE margin reads for the buyer here). A debit costs at most " +
      "what was paid, so loss : gain up to 1.5 keeps the payout worth the price. A view on direction as well as on " +
      "volatility: switch the side to a bear put for a fall. Natural pricing, no earnings inside -- a report props the " +
      "volatility up and collapses it the day after, the wrong way round for a buyer.",
    strategy: "debit_spread",
    side: "call",
    dteMin: "30",
    dteMax: "45",
    deltaMin: "0.35",
    deltaMax: "0.50",
    minOi: "5000",
    wingPoints: "",
    maxSpread: "10",
    earnings: "avoid",
    priceAt: "natural",
    twoExpiries: false,
    filters: {
      ...NO_FILTERS,
      maxLossGain: "1.5",
      minMargin: "3",
      earningsOk: "1",
    },
  },
];

const PRESETS_KEY = "screener.presets";

function loadPresets(): ScreenPreset[] {
  try {
    const raw = getStored(PRESETS_KEY);
    const list = raw ? (JSON.parse(raw) as ScreenPreset[]) : [];
    return Array.isArray(list) ? list.filter((p) => p && typeof p.name === "string") : [];
  } catch {
    return [];
  }
}

/** Whether the next report falls before the expiry, and how far into the
 * position: early is the crush arriving while the strikes are far away,
 * late a gap meeting high gamma and little time. */
function earningsInside(row: ScreenRow): { inside: boolean; share: number | null } {
  if (!row.earnings_date || !row.expiry) return { inside: false, share: null };
  const today = new Date().toISOString().slice(0, 10);
  if (row.earnings_date < today || row.earnings_date > row.expiry) return { inside: false, share: null };
  const span = (Date.parse(row.expiry) - Date.parse(today)) / 864e5;
  const until = (Date.parse(row.earnings_date) - Date.parse(today)) / 864e5;
  return { inside: true, share: span > 0 ? until / span : null };
}

/** "−4.2% · 1.1σ": how far the nearest breakeven sits from the price. */
function toBreakeven(o: ScreenOutcome | null | undefined): string {
  if (!o || o.to_breakeven_pct == null) return "—";
  const p = o.to_breakeven_pct * 100;
  const pctText = `${p > 0 ? "+" : p < 0 ? "−" : ""}${Math.abs(p).toFixed(1)}%`;
  return o.to_breakeven_sigma == null ? pctText : `${pctText} · ${Math.abs(o.to_breakeven_sigma).toFixed(1)}σ`;
}

/** The structure a candidate describes, as the row-shaped value structureOf
 * reads -- a candidate carries the same legs a row does. */
function candidateRow(row: ScreenRow, c: ScreenCandidate): ScreenRow {
  return {
    ...row,
    expiry: c.expiry,
    dte: c.dte,
    short_put: c.short_put,
    short_call: c.short_call,
    put_spread: c.put_spread,
    call_spread: c.call_spread,
    outcome: c.outcome,
  };
}

function candidateStrikes(c: ScreenCandidate): string {
  const parts: string[] = [];
  if (c.put_spread) parts.push(`${formatPrice(c.put_spread.long_strike)}/${formatPrice(c.put_spread.short_strike)}P`);
  else if (c.short_put) parts.push(`${formatPrice(c.short_put.strike)}P`);
  if (c.call_spread) parts.push(`${formatPrice(c.call_spread.short_strike)}/${formatPrice(c.call_spread.long_strike)}C`);
  else if (c.short_call) parts.push(`${formatPrice(c.short_call.strike)}C`);
  return parts.join(" · ");
}

/** The row's structure at the other deltas (and expiry), one line each,
 * valued the same way -- Barchart's many rows per symbol, kept under it. */
function CandidateTable({
  row,
  strategy,
  filters,
  onSelectSymbol,
}: {
  row: ScreenRow;
  strategy: ScreenStrategy;
  filters: ResultFilters;
  onSelectSymbol?: (symbol: string) => void;
}) {
  const all = row.candidates ?? [];
  const cands = all.filter((c) => passesFilters(c.outcome, filters, contextOf(row, strategy)));
  if (cands.length === 0) {
    return <p className="order-hint">None of the {all.length} other candidates meets the filters.</p>;
  }
  return (
    <table className="performance-table screen-candidates">
      <thead>
        <tr>
          <th>Expiry</th>
          <th title={withGloss("Δ", "The short delta this candidate was built at")}>Δ</th>
          <th>Strikes</th>
          <th title={withGloss("Mid nat", "Credit per share at the mid / at the natural")}>Credit mid / nat</th>
          <th>Loss prob</th>
          <th title={withGloss("EV")}>EV</th>
          <th title={withGloss("R/R")}>Loss : Gain</th>
          <th title={withGloss("To BE σ", "How far the nearest breakeven sits from the price")}>To BE</th>
          <th title={withGloss("BE margin", "Breakeven volatility less the forecast, read for the seller")}>BE margin</th>
          <th />
        </tr>
      </thead>
      <tbody>
        {cands.map((c) => {
          const o = c.outcome;
          const shown = candidateRow(row, c);
          const expected = row.forecast_vol ?? row.realised_vol;
          const margin =
            o.breakeven_vol != null && expected != null
              ? (strategy === "debit_spread" ? expected - o.breakeven_vol : o.breakeven_vol - expected) * 100
              : null;
          const structure = structureOf(shown, strategy);
          return (
            <tr key={`${c.expiry}-${c.delta_target}-${candidateStrikes(c)}`}>
              <td>
                {formatExpiry(c.expiry)} ({c.dte}d)
              </td>
              <td>{Math.round(c.delta_target * 100)}Δ</td>
              <td>{candidateStrikes(c)}</td>
              <td>
                {o.credit != null ? o.credit.toFixed(2) : "—"} / {o.credit_natural != null ? o.credit_natural.toFixed(2) : "—"}
              </td>
              <td>{pct(o.loss_probability)}</td>
              <td className={o.expected_value > 0 ? "delta-up" : "delta-down"}>{formatMoney(o.expected_value)}</td>
              <td>{o.risk_reward == null ? "—" : `${o.risk_reward.toFixed(1)} : 1`}</td>
              <td>{toBreakeven(o)}</td>
              <td className={margin == null ? undefined : margin >= 0 ? "delta-up" : "delta-down"}>
                {margin == null ? "—" : `${margin >= 0 ? "+" : ""}${margin.toFixed(1)}`}
              </td>
              <td>
                {structure && (
                  <button
                    type="button"
                    className="timeframe-button"
                    title="Load these strikes into the spread ticket on the Chain tab"
                    onClick={(e) => {
                      e.stopPropagation();
                      onSelectSymbol?.(row.symbol);
                      requestTicket({ symbol: row.symbol, structure });
                    }}
                  >
                    Ticket
                  </button>
                )}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function sortRows(rows: ScreenRow[], by: string | null, desc: boolean): ScreenRow[] {
  const read = by ? SORTS[by] : undefined;
  if (!read) return rows;
  return [...rows].sort((a, b) => {
    const x = read(a);
    const y = read(b);
    if (x == null && y == null) return 0;
    if (x == null) return 1;
    if (y == null) return -1;
    const cmp = typeof x === "string" || typeof y === "string" ? String(x).localeCompare(String(y)) : x - y;
    return desc ? -cmp : cmp;
  });
}

/** A header that sorts when the column has something to sort by. */
function Th({
  label,
  title,
  sort,
  onSort,
}: {
  label: string;
  title?: string;
  sort: { by: string | null; desc: boolean };
  onSort: (label: string) => void;
}) {
  const sortable = label in SORTS;
  const active = sort.by === label;
  return (
    <th
      scope="col"
      title={withGloss(label, title)}
      aria-sort={active ? (sort.desc ? "descending" : "ascending") : undefined}
      className={sortable ? "screen-sortable" : undefined}
      onClick={sortable ? () => onSort(label) : undefined}
    >
      {label}
      {active && <span aria-hidden> {sort.desc ? "▾" : "▴"}</span>}
    </th>
  );
}

function mark(passed: boolean | null): string {
  return passed === null ? "?" : passed ? "✓" : "✗";
}

/** One row's criteria, folded out on click: every measure with what it
 * found, so a "✗" can always be traced to a number. */
function Criteria({ row }: { row: ScreenRow }) {
  return (
    <ul className="screen-criteria">
      {row.criteria.map((c) => (
        <li key={c.key} className={c.passed === null ? "unknown" : c.passed ? "pass" : "fail"}>
          <span className="screen-mark">{mark(c.passed)}</span> <strong>{c.label}:</strong> {c.detail}
        </li>
      ))}
      {row.note && <li className="unknown">{row.note}</li>}
    </ul>
  );
}

/**
 * Which underlyings suit a strategy -- the Optimizer's question one level
 * up (backend app/options/screener.py). The symbols are the watchlist:
 * every row costs a chain fetch, so this screens a list you already keep
 * rather than a universe.
 */
/** One quote width, marked when crossing it costs more than the screen
 * allows. Credit, expectancy and risk-reward are all computed from mids,
 * so a leg quoted 0.21 against 1.31 prices the structure off a number
 * nobody is offering -- this is the value to read before any of them. */
function Width({ fraction, maxSpread, what }: { fraction: number | null; maxSpread: number; what: string }) {
  if (fraction == null) return <>—</>;
  const wide = fraction > maxSpread;
  return (
    <span
      className={wide ? "delta-down" : undefined}
      title={
        wide
          ? `Crossing the ${what}'s quote costs ${pct(fraction)} of its mid, past the ${pct(maxSpread)} this screen allows -- the credit beside it is a mid nobody is offering.`
          : `What crossing the ${what}'s quote costs, as a share of its mid.`
      }
    >
      {pct(fraction)}
    </span>
  );
}

/** Everything about one side of the structure in one cell: the strikes,
 * the width between them, the short's delta, what crossing each of the two
 * quotes costs, and what comes back per dollar of width.
 *
 * It was three columns -- short put, short call, and a single wing -- which
 * repeated the short strike and left two things out. The wing of an iron
 * condor was printed for whichever side won a coin toss, so the row never
 * held all four legs; and the wing's own quote width was in the data and in
 * none of the columns, although it is the leg furthest out of the money and
 * routinely the wider of the two. Measured on 2026-10-01: GOOGL's short put
 * quoted 23 % against 92 % on the wing behind it, NVDA's 41 % against 169 %.
 * The number on screen was the harmless one of the pair. */
function SideCell({
  leg,
  vertical,
  outcome,
  maxSpread,
  side,
}: {
  leg: ScreenShortLeg | null;
  vertical: ScreenVertical | null;
  outcome: ScreenOutcome | null;
  maxSpread: number;
  side: "put" | "call";
}) {
  if (!leg) return <td>—</td>;
  return (
    <td>
      {vertical ? (
        <>
          {formatPrice(vertical.long_strike)}/{formatPrice(vertical.short_strike)} · {vertical.width}w
        </>
      ) : (
        formatPrice(leg.strike)
      )}{" "}
      · Δ{Math.abs(leg.delta).toFixed(2)} ·{" "}
      <Width fraction={leg.spread_fraction} maxSpread={maxSpread} what={`short ${side}`} />
      {vertical && (
        <>
          /<Width fraction={vertical.wing_spread_fraction} maxSpread={maxSpread} what="wing" />
        </>
      )}
      {vertical && (
        <span title="Credit taken in per dollar of width risked. The screen wants at least 10 %.">
          {" · "}
          {pct(vertical.credit_to_width)}
        </span>
      )}
      {outcome && (
        <span
          className={outcome.expected_value > 0 ? "delta-up" : "delta-down"}
          title={`This side valued under the volatility its own strikes trade at. Not half of the EV column: that is one expectation over one distribution, while this says whether this ${side} spread is priced above or below what its own corner of the smile implies. It is usually the two together that explain the structure -- an equity skew makes the put wing dearer in volatility than the short beside it, and the call side rarely gives up as much.`}
        >
          {" · "}
          {formatMoney(outcome.expected_value)}
        </span>
      )}
    </td>
  );
}


// The tab is rebuilt whenever the widget switches away from it, and used to
// come back on Iron condor / Universe every time -- a watchlist screen had to
// be set up again after every glance at the chain. The two choices are kept
// per browser; a blocked or empty storage just means the defaults.
const STORE_STRATEGY = "screener.strategy";
const STORE_UNIVERSE = "screener.universe";
const STORE_SIDE = "screener.side";
const STORE_PRICE_AT = "screener.priceAt";
const STORE_TWO_EXPIRIES = "screener.twoExpiries";
const STORE_ALL_COLUMNS = "screener.allColumns";
const STORE_FILTERS = "screener.filters";
const STORE_SETTINGS_OPEN = "screener.settingsOpen";
const STORE_FILTERS_OPEN = "screener.filtersOpen";
const VERTICAL_LABELS: Record<"credit_spread" | "debit_spread", Record<OptionKind, string>> = {
  credit_spread: { put: "Bull put", call: "Bear call" },
  debit_spread: { put: "Bear put", call: "Bull call" },
};

function stored(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function store(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // Private window or blocked storage: the choice lasts for this visit only.
  }
}

export function ScreenerTab({ onSelectSymbol }: ScreenerTabProps) {
  const { symbols } = useWatchlist();
  const [strategy, setStrategyState] = useState<ScreenStrategy>(() => {
    const saved = stored(STORE_STRATEGY);
    return STRATEGIES.some((b) => b.key === saved) ? (saved as ScreenStrategy) : "iron_condor";
  });
  const setStrategy = (next: ScreenStrategy) => {
    setStrategyState(next);
    store(STORE_STRATEGY, next);
  };
  // The side a vertical is written on; remembered like the strategy.
  const [side, setSideState] = useState<OptionKind>(() => (stored(STORE_SIDE) === "call" ? "call" : "put"));
  const setSide = (next: OptionKind) => {
    setSideState(next);
    store(STORE_SIDE, next);
  };
  const vertical = strategy === "credit_spread" || strategy === "debit_spread";
  const [result, setResult] = useState<ScreenResponse | null>(null);
  // Coloured for the screen the rows came from, not the button pressed since.
  const selling = SELLING_SCREENS.has(result?.strategy ?? strategy);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  // Which rows have their other candidates unfolded.
  const [unfolded, setUnfolded] = useState<Set<string>>(new Set());
  const toggleUnfolded = (symbol: string) =>
    setUnfolded((cur) => {
      const next = new Set(cur);
      if (next.has(symbol)) next.delete(symbol);
      else next.add(symbol);
      return next;
    });
  // Barchart prices at the natural; the mid stays the default here.
  const [priceAt, setPriceAtState] = useState<"mid" | "natural">(() => (stored(STORE_PRICE_AT) === "natural" ? "natural" : "mid"));
  const setPriceAt = (next: "mid" | "natural") => {
    setPriceAtState(next);
    store(STORE_PRICE_AT, next);
  };
  // The essentials by default: can it fill, is the premium rich, what is it
  // worth, how much room. The rest stays one click away (and in each row's
  // criteria, which carry the same numbers).
  const [allColumns, setAllColumnsState] = useState(() => stored(STORE_ALL_COLUMNS) === "1");
  const toggleAllColumns = () =>
    setAllColumnsState((v) => {
      store(STORE_ALL_COLUMNS, v ? "0" : "1");
      return !v;
    });
  // Limits on the rows' own numbers, remembered between visits.
  const [filters, setFiltersState] = useState<ResultFilters>(() => {
    try {
      return { ...NO_FILTERS, ...(JSON.parse(stored(STORE_FILTERS) ?? "{}") as Partial<ResultFilters>) };
    } catch {
      return NO_FILTERS;
    }
  });
  const setFilter = (key: keyof ResultFilters, value: string) =>
    setFiltersState((cur) => {
      const next = { ...cur, [key]: value };
      store(STORE_FILTERS, JSON.stringify(next));
      return next;
    });
  const clearFilters = () => {
    setFiltersState(NO_FILTERS);
    store(STORE_FILTERS, JSON.stringify(NO_FILTERS));
  };
  // Presets: the built-in ones and the reader's own, kept with the other
  // per-user preferences so they follow the login, not the browser.
  const [savedPresets, setSavedPresets] = useState<ScreenPreset[]>(loadPresets);
  const [presetName, setPresetName] = useState("");
  const [activePreset, setActivePreset] = useState<string>("");
  const allPresets = [...BUILT_IN_PRESETS, ...savedPresets];
  // Only the presets of the strategy on screen -- and for a vertical, of its
  // side: a bear call preset is not offered under Bull put.
  const presetsHere = allPresets.filter((p) => p.strategy === strategy && (!vertical || p.side === side));
  useEffect(() => {
    if (activePreset && !presetsHere.some((p) => p.name === activePreset)) setActivePreset("");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [strategy, side]);
  // A second expiry for the candidates: one more chain a symbol.
  const [twoExpiries, setTwoExpiriesState] = useState(() => stored(STORE_TWO_EXPIRIES) === "1");
  const toggleTwoExpiries = () =>
    setTwoExpiriesState((v) => {
      store(STORE_TWO_EXPIRIES, v ? "0" : "1");
      return !v;
    });
  const setTwoExpiries = (on: boolean) => {
    setTwoExpiriesState(on);
    store(STORE_TWO_EXPIRIES, on ? "1" : "0");
  };
  // Watchlist, or the whole tradable universe narrowed by stage one. The
  // universe by default: the watchlist answers "is this one worth it",
  // the universe answers "where is there anything worth it at all", and
  // the second is the question a screener exists for.
  const [universe, setUniverseState] = useState(() => stored(STORE_UNIVERSE) !== "watchlist");
  const toggleUniverse = () =>
    setUniverseState((v) => {
      store(STORE_UNIVERSE, v ? "watchlist" : "universe");
      return !v;
    });
  // The screen's own bounds. The backend has always taken them; until now
  // the tab sent none, so every run was the 30-60 day default -- and on
  // this market that was the difference between +1.54 and +14.10 of
  // expected value, since a 51-day expiry sits behind the whole earnings
  // season and a 16-day one in front of it.
  const [dteMin, setDteMin] = useState("30");
  const [dteMax, setDteMax] = useState("60");
  const [deltaMin, setDeltaMin] = useState("0.10");
  const [deltaMax, setDeltaMax] = useState("0.20");
  const [minOi, setMinOi] = useState("5000");
  // How wide the bought wing sits from the short, in points. Blank leaves
  // it at the backend's 3 % of spot. It is worth having at hand because
  // the width *is* the risk of the vertical: on a 78-dollar ETF the
  // default lands on a one-point wing, 22.50 of profit against 77.50, and
  // eight legs of commission then decide the expectancy.
  const [wingPoints, setWingPoints] = useState("");
  const [helpOpen, setHelpOpen] = useState(false);
  // Null keeps the server's ranking, which is the sensible default; a
  // click takes over from it. Reset whenever a new run arrives, so a sort
  // chosen for one screen does not silently govern the next.
  const [sort, setSort] = useState<{ by: string | null; desc: boolean }>({ by: null, desc: true });
  const onSort = (label: string) =>
    setSort((s) => (s.by === label ? { by: label, desc: !s.desc } : { by: label, desc: true }));
  const [maxSpread, setMaxSpread] = useState("10");
  // Not a checkbox: for a short premium structure *when* the report falls
  // decides everything. Early is the crush arriving while the strikes are
  // still far away; late is a gap meeting high gamma and no time left.
  const [earnings, setEarnings] = useState<"avoid" | "early" | "ignore">("early");

  // The background pass writes one table per strategy every half hour
  // (backend app/options/screen_job.py). Showing it on open is the whole
  // point: a universe screen is a dozen seconds, and nobody should spend
  // them to find out what the market looked like four minutes ago.
  useEffect(() => {
    let cancelled = false;
    setResult(null);
    latestScreen(strategy)
      .then((res) => {
        // The background pass screens the put side; a stored run of the
        // other side would be the wrong table under the right button.
        const storedSide = res.screen?.side ?? "put";
        if (!cancelled && res.screen && (!vertical || storedSide === side)) setResult(res.screen);
      })
      .catch(() => {
        // No stored run is the ordinary case before the first pass.
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [strategy, side]);

  const snapshot = (name: string): ScreenPreset => ({
    name,
    strategy,
    side,
    dteMin,
    dteMax,
    deltaMin,
    deltaMax,
    minOi,
    wingPoints,
    maxSpread,
    earnings,
    priceAt,
    twoExpiries,
    filters,
  });
  const applyPreset = (p: ScreenPreset) => {
    setStrategy(p.strategy);
    setSide(p.side);
    setDteMin(p.dteMin);
    setDteMax(p.dteMax);
    setDeltaMin(p.deltaMin);
    setDeltaMax(p.deltaMax);
    setMinOi(p.minOi);
    setWingPoints(p.wingPoints);
    setMaxSpread(p.maxSpread);
    setEarnings(p.earnings);
    setPriceAt(p.priceAt);
    setTwoExpiries(p.twoExpiries);
    const next = { ...NO_FILTERS, ...p.filters };
    setFiltersState(next);
    store(STORE_FILTERS, JSON.stringify(next));
    setActivePreset(p.name);
  };
  const savePreset = () => {
    const name = presetName.trim();
    if (!name || BUILT_IN_PRESETS.some((b) => b.name === name)) return;
    const next = [...savedPresets.filter((p) => p.name !== name), snapshot(name)];
    setSavedPresets(next);
    setStored(PRESETS_KEY, JSON.stringify(next));
    setActivePreset(name);
    setPresetName("");
  };
  const deletePreset = (name: string) => {
    const next = savedPresets.filter((p) => p.name !== name);
    setSavedPresets(next);
    setStored(PRESETS_KEY, JSON.stringify(next));
    if (activePreset === name) setActivePreset("");
  };
  const [settingsOpen, setSettingsOpen] = useState(() => stored(STORE_SETTINGS_OPEN) === "1");
  const toggleSettings = () =>
    setSettingsOpen((v) => {
      store(STORE_SETTINGS_OPEN, v ? "0" : "1");
      return !v;
    });
  const [filtersOpen, setFiltersOpen] = useState(() => stored(STORE_FILTERS_OPEN) === "1");
  const toggleFilters = () =>
    setFiltersOpen((v) => {
      store(STORE_FILTERS_OPEN, v ? "0" : "1");
      return !v;
    });
  // What the folded settings come to, so they can stay folded.
  const settingsSummary = [
    dteMin.trim() === "0" && dteMax.trim() === "0" ? "0DTE" : `${dteMin}–${dteMax} DTE`,
    `Δ ${deltaMin}–${deltaMax}`,
    `OI ${Number(minOi || 0).toLocaleString()}+`,
    `quotes ≤ ${maxSpread} %`,
    `earnings ${earnings === "early" ? "early ok" : earnings}`,
    priceAt,
    ...(twoExpiries ? ["+1 expiry"] : []),
  ].join(" · ");
  const activeIsOwn = savedPresets.some((p) => p.name === activePreset);
  const activeNote = presetsHere.find((p) => p.name === activePreset)?.note;

  const run = async () => {
    if (!universe && symbols.length === 0) return;
    setLoading(true);
    setError(null);
    try {
      const bounds = {
        strategy,
        ...(vertical ? { side } : {}),
        price_at: priceAt,
        candidate_expiries: (twoExpiries ? 2 : 1) as 1 | 2,
        // 0 is today's expiry (0DTE), not a blank.
        dte_min: numeric(dteMin, 30, true),
        dte_max: numeric(dteMax, 60, true),
        short_delta_min: numeric(deltaMin, 0.1),
        short_delta_max: numeric(deltaMax, 0.2),
        min_open_interest: Math.round(numeric(minOi, 5000)),
        // Typed as a percentage, sent as the fraction the backend wants.
        max_spread_fraction: numeric(maxSpread, 10) / 100,
        earnings_policy: earnings,
        ...(wingPoints.trim() === "" ? {} : { wing_points: numeric(wingPoints, 0) }),
      };
      setResult(
        await screenUnderlyings(
          universe ? { ...bounds, scan_universe: true, limit: 40 } : { ...bounds, symbols: symbols.slice(0, 60) },
        ),
      );
      // A new run comes back in the server's own ranking; a sort chosen for
      // the last one should not quietly govern this one.
      setSort({ by: null, desc: true });
    } catch (err: unknown) {
      setError(err instanceof OrderRejectedError ? err.detail.message : err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="opt-screener">
      <div className="screen-steps">
        {/* 1 -- what to scan */}
        <div className="screen-step">
          <span className="screen-step-n">1</span>
          <span className="screen-step-label">Scan</span>
          <span className="timeframe-selector" role="group" aria-label="What to scan">
            <button
              type="button"
              className="timeframe-button"
              aria-pressed={!universe}
              onClick={() => universe && toggleUniverse()}
              title="The symbols on your watchlist (up to 60) -- for 'is a name I follow worth writing today'. Add symbols in the Watchlist widget."
            >
              Watchlist · {symbols.length} symbol{symbols.length === 1 ? "" : "s"}
            </button>
            <button
              type="button"
              className="timeframe-button"
              aria-pressed={universe}
              onClick={() => !universe && toggleUniverse()}
              title="The whole tradable market, narrowed first to the 40 most traded names in the price band -- for 'is there anything worth writing at all'."
            >
              Universe · top 40 most traded
            </button>
          </span>
          <span className="order-hint">
            {universe
              ? "the market's 40 most traded optionable names, picked fresh each run"
              : symbols.length === 0
                ? "your watchlist is empty -- add symbols in the Watchlist widget, or scan the universe"
                : "only the names you follow"}
          </span>
        </div>

        {/* 2 -- strategy */}
        <div className="screen-step">
          <span className="screen-step-n">2</span>
          <span className="screen-step-label">Strategy</span>
        {STRATEGIES.map((b) => (
          <button
            key={b.key}
            type="button"
            className="timeframe-button"
            aria-pressed={strategy === b.key}
            onClick={() => setStrategy(b.key)}
            title={b.title}
          >
            {b.label}
          </button>
        ))}
        {vertical && (
          <span className="screen-side-toggle">
            {(["put", "call"] as const).map((k) => (
              <button
                key={k}
                type="button"
                className="timeframe-button"
                aria-pressed={side === k}
                onClick={() => setSide(k)}
                title={
                  strategy === "credit_spread"
                    ? k === "put"
                      ? "Bull put: sell a put below the price, buy one further down. Paid if the stock holds above the short strike."
                      : "Bear call: sell a call above the price, buy one further up. Paid if the stock stays below the short strike."
                    : k === "put"
                      ? "Bear put: buy a put, sell one further down. Pays on a fall to the short strike."
                      : "Bull call: buy a call, sell one further up. Pays on a rise to the short strike."
                }
              >
                {VERTICAL_LABELS[strategy as "credit_spread" | "debit_spread"][k]}
              </button>
            ))}
          </span>
        )}
        </div>

        {/* 3 -- preset and settings */}
        <div className="screen-step">
          <span className="screen-step-n">3</span>
          <span className="screen-step-label">Settings</span>
        <label title="The presets of the strategy on screen (for a credit or debit spread, of its side). Each holds every screen setting and result filter; choosing one sets them all, and the screen button runs it.">
          Preset{" "}
          <select
            value={activePreset}
            onChange={(e) => {
              const p = presetsHere.find((x) => x.name === e.target.value);
              if (p) applyPreset(p);
              else setActivePreset("");
            }}
          >
            <option value="">—</option>
            {presetsHere.map((p) => (
              <option key={p.name} value={p.name} title={p.note}>
                {p.builtIn ? "★ " : ""}
                {p.name}
              </option>
            ))}
          </select>
        </label>
          <button
            type="button"
            className="timeframe-button screen-disclosure"
            aria-expanded={settingsOpen}
            onClick={toggleSettings}
            title="DTE window, delta band, open interest, wing, quote limit, earnings rule, mid or natural pricing, a second expiry"
          >
            {settingsOpen ? "▾" : "▸"} {settingsSummary}
          </button>
        </div>
        {activeNote && <p className="order-hint screen-preset-note">{activeNote}</p>}
        {settingsOpen && (
          <div className="opt-preference screen-filters screen-settings-panel">
        <label title={withGloss("DTE", "Days to expiry. The screen takes the listed expiry nearest the middle of this window; past 60 days it asks for the full expiry board.")}>
          DTE{" "}
          <input type="number" min={0} max={400} step={1} value={dteMin} onChange={(e) => setDteMin(e.target.value)} />
          <span aria-hidden>–</span>
          <input type="number" min={0} max={400} step={1} value={dteMax} onChange={(e) => setDteMax(e.target.value)} />
        </label>
        <button
          type="button"
          className="timeframe-button"
          aria-pressed={dteMin.trim() === "0" && dteMax.trim() === "0"}
          title="Today's expiry only (0DTE). Only names with daily expiries list one -- SPY, QQQ, IWM and a few more; the rest come back with no expiry in the window."
          onClick={() => {
            setDteMin("0");
            setDteMax("0");
          }}
        >
          0DTE
        </button>
        <label title={withGloss("Δ", "The band the short strike's delta must fall in. 0.10-0.20 is the conventional premium-selling range; higher takes more premium and more risk.")}>
          Δ{" "}
          <input type="number" min={0.01} max={0.99} step={0.01} value={deltaMin} onChange={(e) => setDeltaMin(e.target.value)} />
          <span aria-hidden>–</span>
          <input type="number" min={0.01} max={0.99} step={0.01} value={deltaMax} onChange={(e) => setDeltaMax(e.target.value)} />
        </label>
        <label title={withGloss("OI", "Open interest across the screened expiry's fetched strikes -- one expiry's, an order of magnitude below a whole-chain number.")}>
          OI{" "}
          <input type="number" min={0} step={500} value={minOi} onChange={(e) => setMinOi(e.target.value)} />
        </label>
        <label title="How far the bought wing sits from the short strike, in points. Blank aims at 3 % of spot, which on a cheap ETF rounds down to a one-point wing. The width is the whole risk of the vertical, so widening it changes the credit-to-width ratio and the expectancy with it.">
          Wing{" "}
          <input
            type="number"
            min={0.5}
            step={0.5}
            placeholder="3 %"
            value={wingPoints}
            onChange={(e) => setWingPoints(e.target.value)}
          />
        </label>
        <label title="How wide the quote at the wider short leg may be, as a percentage of its mid.">
          Quote ≤{" "}
          <input type="number" min={1} max={100} step={1} value={maxSpread} onChange={(e) => setMaxSpread(e.target.value)} />
          <span aria-hidden> %</span>
        </label>
        <label title="What a report inside the expiry means. Early: one in the first third of the position's life passes -- the implied volatility that made the credit fat collapses while the strikes are still far away, and weeks of decay follow. Avoid: any report inside fails. Ignore: reported, not judged.">
          Earnings{" "}
          <select value={earnings} onChange={(e) => setEarnings(e.target.value as "avoid" | "early" | "ignore")}>
            <option value="early">early ok</option>
            <option value="avoid">avoid</option>
            <option value="ignore">ignore</option>
          </select>
        </label>
        <span className="screen-side-toggle">
          {(["mid", "natural"] as const).map((k) => (
            <button
              key={k}
              type="button"
              className="timeframe-button"
              aria-pressed={priceAt === k}
              onClick={() => setPriceAt(k)}
              title={
                k === "mid"
                  ? "Credit, EV and the extremes at the mids; the chance of profit is net of crossing the quotes either way."
                  : "Credit, EV and the extremes at the natural -- every leg at its bid or ask, Barchart's convention and the honest number for a limit meant to fill at once."
              }
            >
              {k === "mid" ? "Mid" : "Natural"}
            </button>
          ))}
        </span>
            <button
              type="button"
              className="timeframe-button"
              aria-pressed={twoExpiries}
              onClick={toggleTwoExpiries}
              title="Besides each row's own structure the screen lists it at 10, 16 and 25 delta on the same expiry (no extra cost). This adds the same on a second expiry in the window -- one more chain fetch per symbol."
            >
              +1 expiry
            </button>
          </div>
        )}

        {/* 4 -- run */}
        <div className="screen-step">
          <span className="screen-step-n">4</span>
          <button
            type="button"
            className="generate-button opt-run"
            disabled={loading || (!universe && symbols.length === 0)}
            onClick={() => void run()}
          >
            {loading ? "Reading chains…" : `Scan ${universe ? "the universe" : `${Math.min(symbols.length, 60)} watchlist symbols`}`}
          </button>
          <span className="order-hint">
            for {structureName(strategy, side)} · one option chain per symbol, about {universe ? "15-30" : `${Math.max(5, Math.round(Math.min(symbols.length, 60) * 0.4))}`} s
          </span>
          <button
            type="button"
            className="timeframe-button options-help-button"
            onClick={() => setHelpOpen(true)}
            title="How to read a screen, and what to try when nothing passes"
            aria-label="How to read a screen"
          >
            ?
          </button>
        </div>
      </div>
      <ScreenerHelp open={helpOpen} onClose={() => setHelpOpen(false)} strategy={strategy} />

      {!result && !loading && (
        <div className="screen-guide">
          <strong>How to screen:</strong> 1 · choose what to scan -- your <em>watchlist</em> for names you follow, or the{" "}
          <em>universe</em> for the market's most traded names. 2 · pick a strategy (and, for a spread, its side). 3 · optionally
          pick a ★ preset, which sets everything else. 4 · press <em>Scan</em>. Each row is the best structure for one symbol;
          click a row for every criterion, ▸ for the same structure at other deltas, <em>Ticket</em> to load it.
        </div>
      )}

      {/* the results' own tools: filters, columns, saving the setup */}
      {result && (
        <div className="screen-results-bar">
          <button
            type="button"
            className="timeframe-button screen-disclosure"
            aria-expanded={filtersOpen}
            onClick={toggleFilters}
            title="Limit the rows on screen by each structure's own numbers -- applied at once, also to a stored run"
          >
            {filtersOpen ? "▾" : "▸"} Filter results{filtersActive(filters) ? ` (${activeFilterCount(filters)} active)` : ""}
          </button>
          <button
            type="button"
            className="timeframe-button"
            aria-pressed={allColumns}
            onClick={toggleAllColumns}
            title="Off: the essentials -- fillable, premium rich, the structure, its odds and worth, the room to the breakeven. On: every column, IV and realised vol, the cone, open interest and volume, the account risk."
          >
            All columns
          </button>
          <span className="screen-save">
        {activeIsOwn && (
          <button type="button" className="timeframe-button" onClick={() => deletePreset(activePreset)} title="Delete this saved preset">
            Delete
          </button>
        )}
        <input
          type="text"
          className="screen-preset-name"
          placeholder="Save current as…"
          value={presetName}
          maxLength={60}
          onChange={(e) => setPresetName(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") savePreset();
          }}
        />
        <button
          type="button"
          className="timeframe-button"
          disabled={!presetName.trim() || BUILT_IN_PRESETS.some((b) => b.name === presetName.trim())}
          onClick={savePreset}
          title="Save the strategy, side, screen bounds, pricing and result filters as they are now. A name already saved is replaced; a built-in name is taken."
        >
          Save
        </button>
          </span>
        </div>
      )}
      {result && filtersOpen && (
        <div className="opt-preference screen-filters screen-result-filters">
        <label title={withGloss("Loss ≤", "Loss probability at most, in percent -- the share of the implied distribution under which the structure ends in a loss (net of crossing the quotes).")}>
          Loss ≤{" "}
          <input type="number" min={0} max={100} step={1} placeholder="%" value={filters.maxLossProb} onChange={(e) => setFilter("maxLossProb", e.target.value)} />
          <span aria-hidden> %</span>
        </label>
        <label title="Maximum profit per contract at least, in dollars -- for a credit structure, the credit.">
          Profit ≥{" "}
          <input type="number" min={0} step={5} placeholder="$" value={filters.minProfit} onChange={(e) => setFilter("minProfit", e.target.value)} />
        </label>
        <label title="Maximum loss per contract at most, in dollars -- the width less the credit for a credit spread.">
          Max loss ≤{" "}
          <input type="number" min={0} step={50} placeholder="$" value={filters.maxLoss} onChange={(e) => setFilter("maxLoss", e.target.value)} />
        </label>
        <label title="Loss : gain at most -- 5 keeps structures risking up to five dollars per dollar they can make.">
          Loss : Gain ≤{" "}
          <input type="number" min={0} step={0.5} placeholder=": 1" value={filters.maxLossGain} onChange={(e) => setFilter("maxLossGain", e.target.value)} />
        </label>
        <label title={withGloss("EV ≥", "Expected value per contract at least, in dollars. 0 keeps only structures worth something under the market's own odds.")}>
          EV ≥{" "}
          <input type="number" step={5} placeholder="$" value={filters.minEv} onChange={(e) => setFilter("minEv", e.target.value)} />
        </label>
        <label title={withGloss("To BE ≥", "The nearest breakeven at least this many standard deviations from the price -- 1 is about where a 16-delta strike sits.")}>
          To BE ≥{" "}
          <input type="number" min={0} step={0.1} placeholder="σ" value={filters.minToBe} onChange={(e) => setFilter("minToBe", e.target.value)} />
          <span aria-hidden> σ</span>
        </label>
        <label title={withGloss("BE margin ≥", "Breakeven volatility at least this many points past the forecast (for a debit: below it) -- the margin for error, and where a premium seller's edge comes from.")}>
          BE margin ≥{" "}
          <input type="number" step={1} placeholder="pts" value={filters.minMargin} onChange={(e) => setFilter("minMargin", e.target.value)} />
        </label>
        <label title={withGloss("IV/RV ≥", "Implied over realised volatility at least. 1.2 is where the screen calls the premium rich.")}>
          IV/RV ≥{" "}
          <input type="number" min={0} step={0.05} placeholder="×" value={filters.minIvRv} onChange={(e) => setFilter("minIvRv", e.target.value)} />
        </label>
        <label title="Only rows whose earnings rule passed. Under 'early ok' a report late in the position's life fails the rule but the row is still listed; this takes it out. Rows with no known date (most ETFs) stay.">
          <input
            type="checkbox"
            checked={filters.earningsOk === "1"}
            onChange={(e) => setFilter("earningsOk", e.target.checked ? "1" : "")}
          />{" "}
          Earnings ok
        </label>
          {filtersActive(filters) && (
            <button type="button" className="timeframe-button" onClick={clearFilters}>
              Clear
            </button>
          )}
        </div>
      )}
      {error && <p className="order-rejection">{error}</p>}
      {!universe && symbols.length === 0 && <p className="widget-empty">Add symbols to the watchlist to screen them.</p>}

      {result?.stored_at && (
        <p className="order-hint">
          Stored run from {new Date(result.stored_at).toLocaleTimeString()} ({ageOf(result.stored_at)}) · the background
          pass screens the universe every half hour. Press the button for a live one.
        </p>
      )}
      {result?.stored_at && result.criteria.earnings_policy !== earnings && (
        <p className="order-rejection">
          This stored run treated earnings as "{result.criteria.earnings_policy}", not "{earnings}" as set above
          {earnings === "avoid" ? " -- rows holding a report before their expiry are still in it" : ""}. Run the screen to
          apply your setting.
        </p>
      )}
      {result?.excluded_no_credit && result.excluded_no_credit.length > 0 && (
        <p
          className="order-hint"
          title="A written structure that takes nothing in once every leg is crossed -- the short's bid at or under the wing's ask. Not tradable for a credit, so not a row; on quiet names the quotes are often wider than the premium."
        >
          Left out, no credit at the natural ({result.excluded_no_credit.length}): {result.excluded_no_credit.join(", ")}
        </p>
      )}
      {result?.excluded_by_earnings && result.excluded_by_earnings.length > 0 && (
        <p className="order-hint" title="Under avoid, a symbol whose every expiry in the window holds its next report is not a row: there is no structure in the window that misses the print.">
          Left out for earnings ({result.excluded_by_earnings.length}):{" "}
          {result.excluded_by_earnings
            .map((x) => `${x.symbol}${x.earnings_date ? ` ${formatExpiry(x.earnings_date)}` : ""}`)
            .join(", ")}
        </p>
      )}
      {result && (
        <>
          {result.preselection && (
            <p className="order-hint">
              Stage one: {result.preselection.considered.toLocaleString()} symbols considered,{" "}
              {result.preselection.selected} priced
              {Object.keys(result.preselection.dropped).length > 0
                ? ` · dropped ${Object.entries(result.preselection.dropped)
                    .map(([reason, n]) => `${n} ${reason.replace(/_/g, " ")}`)
                    .join(", ")}`
                : ""}
            </p>
          )}
          <p className="order-hint">
            {result.criteria.dte[0]}–{result.criteria.dte[1]} DTE · OI {result.criteria.min_open_interest.toLocaleString()}+ ·
            quotes under {pct(result.criteria.max_spread_fraction)} · short delta {result.criteria.short_delta[0].toFixed(2)}–
            {result.criteria.short_delta[1].toFixed(2)}
            {result.criteria.earnings_policy === "avoid"
              ? " · no earnings inside"
              : result.criteria.earnings_policy === "early"
                ? ` · earnings only in the first ${Math.round(result.criteria.early_earnings_fraction * 100)} % of the position`
                : ""}
          </p>
          {result.rows.length > 0 && result.rows.every((r) => r.expiry == null) && (
            <div className="screen-guide">
              <strong>
                No symbol lists an expiry {result.criteria.dte[0]}–{result.criteria.dte[1]} days out.
              </strong>{" "}
              {result.criteria.dte[1] === 0 ? (
                <>
                  A 0DTE screen needs an expiry today: on a weekend or a market holiday there is none, and only names
                  with daily expiries (SPY, QQQ, IWM) have one every session. Run it on a trading day -- best after
                  10:00 ET -- or set DTE to 1–3 to see the next session's expiry.
                </>
              ) : (
                <>Widen the DTE window in step 3.</>
              )}
            </div>
          )}
          {filtersActive(filters) &&
            result.rows.length > 0 &&
            !result.rows.some((r) => passesFilters(r.outcome, filters, contextOf(r, result.strategy))) &&
            (() => {
              const misses = nearMisses(result.rows, filters, result.strategy);
              return (
                <div className="screen-guide">
                  <strong>No row meets every filter.</strong>{" "}
                  {misses.length > 0 ? (
                    <>
                      Missing by one: {misses.map((m) => `${m.symbol} (${m.label})`).join(", ")}. Loosen that filter, or
                      clear them all.
                    </>
                  ) : (
                    <>Every row misses by two or more -- the line below says which filter removes most.</>
                  )}{" "}
                  <button type="button" className="link-button" onClick={clearFilters}>
                    Clear filters
                  </button>
                </div>
              );
            })()}
          {filtersActive(filters) && (
            <p className="order-hint">
              {result.rows.filter((r) => passesFilters(r.outcome, filters, contextOf(r, result.strategy))).length} of{" "}
              {result.rows.length} rows meet the filters · each on its own removes{" "}
              {filterFunnel(result.rows, filters, result.strategy)
                .map((f) => `${f.label} ${f.removed}`)
                .join(", ")}
              {result.excluded_by_earnings && result.excluded_by_earnings.length > 0
                ? ` · ${result.excluded_by_earnings.length} more left out for earnings before the screen`
                : ""}{" "}
              ·{" "}
              <button type="button" className="link-button" onClick={clearFilters}>
                clear
              </button>
            </p>
          )}
          <table className="opt-table screen-table">
            <thead>
              <tr>
                <Th label="Symbol" sort={sort} onSort={onSort} />
                <Th label="Passed" title="Criteria met out of those that could be judged. The server's own ranking, and the default order." sort={sort} onSort={onSort} />
                <Th
                  label="Worst leg"
                  title="The widest quote anywhere in the structure, as a share of its own mid, with P or C for the side it sits on. A package fills at the price of its worst leg, so sorting this ascending puts what can actually be traded on top -- which is the question to settle before reading an expectancy. The side matters too: a wide put wing does not stand in the way of a trade that only sells the call side. Hover the letter for the exact leg. Red past the limit set above."
                  sort={sort}
                  onSort={onSort}
                />
                {allColumns && (
                  <>
                    <Th label="IV" title="At-the-money implied volatility of the screened expiry." sort={sort} onSort={onSort} />
                    <Th label="RV" title="Close-to-close volatility of the last 20 sessions, annualised." sort={sort} onSort={onSort} />
                  </>
                )}
                <Th label="IV/RV" title="Implied over realised. Above 1.2 the premium is rich, below 0.95 it is cheap." sort={sort} onSort={onSort} />
                <Th label="IV rank" title="Where today's IV sits in this symbol's own recorded range. Needs 20 sessions." sort={sort} onSort={onSort} />
                {allColumns && (
                  <>
                    <Th label="Cone" title={CONE_TITLE} sort={sort} onSort={onSort} />
                    <Th label="OI" title="Open interest across the fetched strikes of that expiry." sort={sort} onSort={onSort} />
                    <Th
                      label="Vol/OI"
                      title="Contracts traded today against the positions already open. Open interest says a crowd is positioned; this says whether anyone is still trading it. Above 1 the expiry is being built today rather than carried."
                      sort={sort}
                      onSort={onSort}
                    />
                  </>
                )}
                <Th label="Expiry" title="Sorts by days to expiry." sort={sort} onSort={onSort} />
                <Th
                  label="Earnings"
                  title="The next report. Red when it falls before the expiry -- the reason the premium is rich, and the move the structure has to survive -- with how far into the position it comes. Sorts the reports inside the expiry first."
                  sort={sort}
                  onSort={onSort}
                />
                {sideColumns(result).map((col) => (
                  <Th
                    key={col.kind}
                    label={col.label}
                    title={
                      col.label.startsWith("Short")
                        ? `The short ${col.kind}: strike · delta · what crossing its quote costs against its mid (red past the limit set above). Sorts by delta.`
                        : `long/short · width · delta of the short · what crossing the short's quote costs / what crossing the wing's costs · credit per dollar of width · what this ${col.kind} spread is worth under the volatility its own strikes trade at. The two quote widths are the ones to read first: everything priced after them is computed from mids, so a wide quote makes the rest fiction. Either is marked red past the limit set above. Sorts by its own expectancy.`
                    }
                    sort={sort}
                    onSort={onSort}
                  />
                ))}
                {result.rows.some((r) => r.outcome) && (
                  <>
                    <Th
                      label="Loss : Gain"
                      title="The most it can lose against the most it can make, per contract. 7 : 1 means seven dollars at risk for every one it pays -- a ratio a high chance of profit has to make up for, which is what the EV says whether it does."
                      sort={sort}
                      onSort={onSort}
                    />
                    <th scope="col" title="Max profit and max loss of the structure named in this row, per contract.">
                      Profit / Loss
                    </th>
                    <Th label="Loss prob" title="Share of the option market's own implied distribution at expiry under which the structure loses." sort={sort} onSort={onSort} />
                    <Th
                      label="EV"
                      title="The payoff weighted by that distribution, over one distribution built from the at-the-money volatility. A structure can pay a large credit, lose rarely, and still be negative here -- which is what a list sorted by max profit hides."
                      sort={sort}
                      onSort={onSort}
                    />
                    <Th
                      label="BE vol"
                      title={withBook(
                        "Breakeven volatility: the at-the-money level at which the model values the structure at its mid credit (or debit). In brackets its distance from the volatility expected over the expiry's life (the 20-session reading blended toward the past year's, more so the longer the expiry) -- the margin for error: how far the stock's movement may come out above (for a credit) or below (for a debit) that forecast before the price no longer pays for it. Green while the forecast is on the right side.",
                        NATENBERG.marginForError,
                        NATENBERG.volForecasting,
                      )}
                      sort={sort}
                      onSort={onSort}
                    />
                    <Th
                      label="To BE"
                      title="How far the nearest breakeven sits from the price, in percent and in standard deviations to expiry at the at-the-money IV. Sorts by the sigmas."
                      sort={sort}
                      onSort={onSort}
                    />
                    {allColumns && (
                      <Th
                        label="Risk %"
                        title="The max loss against your account's equity -- the size decision, as opposed to R/R, which is the structure's own shape. The criterion fails past 2 %."
                        sort={sort}
                        onSort={onSort}
                      />
                    )}
                  </>
                )}
                {allColumns && result.rows.some((r) => r.term_ratio != null) && (
                  <th scope="col" title="The front expiry's implied volatility over the back one's.">Front/back</th>
                )}
                <th scope="col" aria-label="Actions" />
              </tr>
            </thead>
            <tbody>
              {sortRows(
                result.rows.filter((r) => passesFilters(r.outcome, filters, contextOf(r, result.strategy))),
                sort.by,
                sort.desc,
              ).map((row) => (
                <>
                  <tr
                    key={row.symbol}
                    className={`screen-row${open === row.symbol ? " open" : ""}`}
                    onClick={() => setOpen(open === row.symbol ? null : row.symbol)}
                  >
                    <th scope="row">
                      <button
                        type="button"
                        className="link-button"
                        onClick={(e) => {
                          e.stopPropagation();
                          onSelectSymbol?.(row.symbol);
                        }}
                        title="Load this symbol's chain"
                      >
                        {row.symbol}
                      </button>
                    </th>
                    <td className={row.passed === row.scored ? "delta-up" : undefined}>
                      {row.passed}/{row.scored}
                    </td>
                    <td>
                      {(() => {
                        const worst = worstLeg(row);
                        if (!worst) return "—";
                        return (
                          <>
                            <Width fraction={worst.fraction} maxSpread={result.criteria.max_spread_fraction} what={worst.what} />
                            {/* Which side it sits on, because that decides
                                whether the constraint applies to you at all:
                                a wide put wing does not stop a trade that
                                only sells the call side. */}
                            <span className="opt-worst-side" title={`The widest quote in this structure is the ${worst.what}.`}>
                              {" "}
                              {worst.side}
                            </span>
                          </>
                        );
                      })()}
                    </td>
                    {allColumns && (
                      <>
                        <td>{pct(row.atm_iv)}</td>
                        <td>{pct(row.realised_vol)}</td>
                      </>
                    )}
                    <td
                      className={toneClass(sideTone(ratioTone(row.iv_rv_ratio), selling))}
                      title={
                        row.ref_iv != null && row.ref_expiry
                          ? withBook(`Judged on ${formatExpiry(row.ref_expiry)} (${row.ref_dte} d), IV ${pct(row.ref_iv)}: the ${row.dte}-day chain screened is outside the 20-90 day band, and a chain that near prices the next few sessions rather than the stock. The IV rank is read on the same expiry.`, NATENBERG.historicalVol, NATENBERG.ivAsPredictor)
                          : withBook(`At-the-money IV over the stock's realised volatility of the last 20 sessions. From ${RICH_IV_RATIO.toFixed(2)} rich, up to ${CHEAP_IV_RATIO.toFixed(2)} cheap -- green where that suits this screen's side (rich for selling, cheap for buying), red where it works against it, as in the ticket.`, NATENBERG.historicalVol, NATENBERG.ivAsPredictor)
                      }
                    >
                      {row.iv_rv_ratio == null ? "—" : `${row.iv_rv_ratio.toFixed(2)}×`}
                      {row.ref_iv != null && <span className="screen-ref"> {row.ref_dte}d</span>}
                    </td>
                    <td
                      className={toneClass(sideTone(ivTone(row.iv_rank), selling))}
                      title={
                        row.iv_rank == null
                          ? `${row.iv_rank_samples} sessions recorded, 20 needed`
                          : withBook(`Today's IV at ${row.iv_rank.toFixed(0)} % of its 52-week range (${row.iv_rank_samples} sessions). From 60 % rich against its own year, up to 30 % cheap -- coloured like IV/RV for this screen's side, on the IV-rank bands used everywhere else.`, NATENBERG.ivAsPredictor)
                      }
                    >
                      {row.iv_rank == null ? "—" : `${row.iv_rank.toFixed(0)} %`}
                    </td>
                    {allColumns && (
                      <>
                        <td title={row.cone_pct == null ? "Less than a year of closes" : `Above ${row.cone_pct.toFixed(0)} % of ${row.cone_window}-session spells this year`}>
                          {row.cone_pct == null ? "—" : `${row.cone_pct.toFixed(0)} %`}
                        </td>
                        <td title={row.open_interest == null ? "Not reported for this expiry right now" : undefined}>
                          {row.open_interest == null ? "—" : row.open_interest.toLocaleString()}
                        </td>
                        <td className={row.volume_oi_ratio != null && row.volume_oi_ratio >= 1 ? "delta-up" : undefined}>
                          {row.volume_oi_ratio == null ? "—" : `${row.volume_oi_ratio.toFixed(2)}×`}
                        </td>
                      </>
                    )}
                    <td>{row.expiry ? `${row.expiry} (${row.dte}d)` : "—"}</td>
                    {(() => {
                      const e = earningsInside(row);
                      return (
                        <td
                          className={e.inside ? "delta-down" : undefined}
                          title={
                            !row.earnings_date
                              ? "No report date known"
                              : e.inside
                                ? `Reports ${row.earnings_date}, before this expiry${e.share != null ? ` -- ${Math.round(e.share * 100)} % into the position` : ""}`
                                : `Reports ${row.earnings_date}, after this expiry`
                          }
                        >
                          {row.earnings_date ? `${e.inside ? "⚠ " : ""}${formatExpiry(row.earnings_date)}` : "—"}
                        </td>
                      );
                    })()}
                    {sideColumns(result).map((col) => (
                      <SideCell
                        key={col.kind}
                        leg={col.kind === "put" ? row.short_put : row.short_call}
                        vertical={col.kind === "put" ? row.put_spread : row.call_spread}
                        outcome={col.kind === "put" ? row.put_outcome : row.call_outcome}
                        maxSpread={result.criteria.max_spread_fraction}
                        side={col.kind}
                      />
                    ))}
                    {result.rows.some((r) => r.outcome) && (
                      <>
                        <td>{row.outcome?.risk_reward == null ? "—" : `${row.outcome.risk_reward.toFixed(1)} : 1`}</td>
                        <td>
                          {row.outcome
                            ? `${formatMoney(row.outcome.max_profit ?? 0)} / ${formatMoney(row.outcome.max_loss ?? 0)}`
                            : "—"}
                        </td>
                        <td>{row.outcome ? pct(row.outcome.loss_probability) : "—"}</td>
                        <td className={row.outcome ? (row.outcome.expected_value > 0 ? "delta-up" : "delta-down") : undefined}>
                          {row.outcome ? formatMoney(row.outcome.expected_value) : "—"}
                        </td>
                        <BreakevenVolCell row={row} buyer={result.strategy === "debit_spread"} />
                        <td>{toBreakeven(row.outcome)}</td>
                        {allColumns && (
                          <td className={row.risk_share != null && row.risk_share > 0.02 ? "delta-down" : undefined}>
                            {row.risk_share == null ? "—" : pct(row.risk_share, 1)}
                          </td>
                        )}
                      </>
                    )}
                    {allColumns && result.rows.some((r) => r.term_ratio != null) && (
                      <td>{row.term_ratio == null ? "—" : `${row.term_ratio.toFixed(2)}×`}</td>
                    )}
                    <td className="screen-actions">
                      {(row.candidates?.length ?? 0) > 0 && (
                        <button
                          type="button"
                          className="timeframe-button"
                          aria-pressed={unfolded.has(row.symbol)}
                          title="The same structure at 10, 16 and 25 delta (and on a second expiry with +1 expiry), valued the same way"
                          onClick={(e) => {
                            e.stopPropagation();
                            toggleUnfolded(row.symbol);
                          }}
                        >
                          {unfolded.has(row.symbol) ? "▾" : "▸"} {row.candidates!.length}
                        </button>
                      )}
                      {(() => {
                        const structure = structureOf(row, result.strategy);
                        return structure ? (
                          <button
                            type="button"
                            className="timeframe-button"
                            title="Load these strikes into the spread ticket on the Chain tab"
                            onClick={(e) => {
                              e.stopPropagation();
                              onSelectSymbol?.(row.symbol);
                              requestTicket({ symbol: row.symbol, structure });
                            }}
                          >
                            Ticket
                          </button>
                        ) : null;
                      })()}
                      <button
                        type="button"
                        className="timeframe-button"
                        title="Rank the structures for this symbol in the Optimizer, on the expiry and family this screen used"
                        onClick={(e) => {
                          e.stopPropagation();
                          const intent = OPTIMIZER_INTENT[result.strategy];
                          onSelectSymbol?.(row.symbol);
                          requestOptimizer({
                            symbol: row.symbol,
                            outlook: intent.outlook,
                            reason: reasonFor(row),
                            request: {
                              strategies: intent.strategies,
                              ...(intent.income ? { budget: null } : {}),
                              ...(row.expiry ? { horizon_expiry: row.expiry } : {}),
                            },
                          });
                        }}
                      >
                        Optimize
                      </button>
                    </td>
                  </tr>
                  {unfolded.has(row.symbol) && (row.candidates?.length ?? 0) > 0 && (
                    <tr key={`${row.symbol}-candidates`} className="screen-detail">
                      <td colSpan={40}>
                        <CandidateTable row={row} strategy={result.strategy} filters={filters} onSelectSymbol={onSelectSymbol} />
                      </td>
                    </tr>
                  )}
                  {open === row.symbol && (
                    <tr key={`${row.symbol}-criteria`} className="screen-detail">
                      <td colSpan={40}>
                        <Criteria row={row} />
                      </td>
                    </tr>
                  )}
                </>
              ))}
            </tbody>
          </table>
          <p className="order-hint">{result.disclaimer}</p>
        </>
      )}
      <div className="screen-import">
        <BarchartImportPanel onSelectSymbol={onSelectSymbol} />
      </div>
    </div>
  );
}
