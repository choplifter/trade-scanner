/** The Earnings screen -- backend app/routers/earnings.py.
 *
 * Three calls, deliberately separate because they cost very different
 * amounts: the list is a calendar intersected with what Alpaca trades
 * (milliseconds), the facts read one chain per symbol (a second or two
 * for a screenful), and an evaluation reads two chains, the GEX profile
 * and prices a dozen structures (several seconds, one symbol at a time).
 */

import type { OptimizeRequest, OptimizeResponse, Strategy } from "./options";

/** One reporter. `session` says which of the two days it falls on;
 * `reported` is true once the company has actually published, which is
 * the difference between an event ahead and one already paid out. */
export interface EarningsScreenRow {
  symbol: string;
  report_date: string;
  session: "today" | "next";
  reported: boolean;
  company_name: string | null;
  /** The live price when the scanner has a row for it, else the last close. */
  last: number | null;
  avg_dollar_vol_20d: number;
  dollar_volume_today: number | null;
  pct_change: number | null;
  market_cap: number | null;
  sector: string | null;
}

export interface EarningsScreenResponse {
  today: string;
  next_session: string;
  min_dollar_volume: number;
  rows: EarningsScreenRow[];
  /** Reporters the calendar named before the liquidity cut. */
  candidates: number;
  /** False for `calendar` means FMP could not be asked -- an empty list
   * is then "not known", not "nobody reports". */
  sources: { calendar: boolean; bars: boolean };
}

/** Per symbol, the two numbers the screen is really about: what the
 * market charges for this print, and what the stock has actually done
 * over its past ones. Every field may be null; null is "not known". */
export interface SymbolFacts {
  symbol: string;
  front_expiry: string | null;
  front_dte: number | null;
  contract_count: number | null;
  atm_iv: number | null;
  /** One sigma to the front expiry as a percent of spot. On the report
   * day this includes the ordinary sessions to expiry, not the print
   * alone -- there is no pre-report expiry to net out. */
  implied_move_pct: number | null;
  hist_median_pct: number | null;
  hist_mean_pct: number | null;
  hist_max_pct: number | null;
  samples: number;
  history_note: string | null;
}

export interface EarningsFactsResponse {
  today: string;
  facts: Record<string, SymbolFacts>;
}

/** What the rule matrix read. Nulls are unknowns and never counted as
 * evidence -- see backend app/options/strategy_select.py. */
export interface EarningsSignals {
  spot: number;
  implied_move_pct: number | null;
  hist_median_pct: number | null;
  hist_max_pct: number | null;
  samples: number;
  /** Implied move over the median past one; above 1.15 the market is
   * charging more than the stock has delivered. */
  move_ratio: number | null;
  iv_over_realized: number | null;
  iv_rank_pct: number | null;
  iv_rank_samples: number;
  front_iv: number | null;
  back_iv: number | null;
  term_slope: number | null;
  skew: number | null;
  gex_regime: string | null;
  near_flip: boolean | null;
  call_wall: number | null;
  put_wall: number | null;
  offerable_puts_below: number;
  offerable_calls_above: number;
  wing_room: boolean | null;
  market_level: string | null;
  reported: boolean;
  realized_vol_20d: number | null;
  avg_daily_range_pct: number | null;
  term_structure: { expiry: string; dte: number; atm_iv: number | null; skew: number | null }[];
  gex: Record<string, unknown> | null;
}

/** One family's points and the sentences that produced them. `priced`
 * false means this path did not price it and says why -- `open_optimizer`
 * is then the preset that would. */
export interface FamilyScore {
  family: Strategy;
  label: string;
  group: "neutral" | "volatility" | "time" | "directional";
  score: number;
  reasons: string[];
  priced: boolean;
  not_priced_because?: string;
  open_optimizer: Partial<OptimizeRequest> | null;
}

/** An optimizer run, or the reason there was none. The evaluation stands
 * either way: a pricing failure does not cost the reader the signals. */
export type EarningsOptimizerRun = OptimizeResponse | { error: string } | null;

export interface EarningsEvaluation {
  symbol: string;
  spot: number;
  as_of: string;
  report_date: string | null;
  days_until: number | null;
  reported: boolean;
  horizon_expiry: string;
  horizon_dte: number;
  back_expiry: string | null;
  signals: EarningsSignals;
  market: { level: string | null; reasons: string[] };
  scores: FamilyScore[];
  picks: Strategy[];
  target: {
    neutral: { families: Strategy[]; move: number; basis: string } | null;
    volatility: { families: Strategy[]; move: number; basis: string } | null;
  };
  warnings: string[];
  optimizer: { neutral: EarningsOptimizerRun; volatility: EarningsOptimizerRun };
  disclaimer: string;
}

/** Narrowing helper: an optimizer run that actually priced something. */
export function isPriced(run: EarningsOptimizerRun): run is OptimizeResponse {
  return run != null && !("error" in run);
}
