import type { ScannerRow } from "./alpaca";

/** Field types the backend registry uses. Drives which operators a field
 * offers and how its values are formatted -- see app/scanners/screener.py. */
export type FieldType = "number" | "percent" | "currency" | "boolean" | "text";

export type Operator =
  | "gt"
  | "gte"
  | "lt"
  | "lte"
  | "between"
  | "eq"
  | "ne"
  | "contains"
  | "in"
  | "is_true"
  | "is_false";

/** One screenable column, exactly as the server describes it. The UI builds
 * its field pickers, operator lists and column chooser from these rather
 * than hardcoding any of it -- adding a field on the backend adds it here
 * with no frontend change. */
export interface FieldSpec {
  name: string;
  label: string;
  type: FieldType;
  operators: Operator[];
  /** True for fields that aren't ScannerRow attributes (float, rank score).
   * Their values arrive in ScreenResponse.derived, keyed by symbol, rather
   * than on the row itself. */
  derived: boolean;
}

export interface ScreenFilter {
  field: string;
  op: Operator;
  value?: string | number | string[] | null;
  value2?: string | number | null;
}

export interface Screen {
  filters: ScreenFilter[];
  sort_by: string;
  descending: boolean;
  limit: number;
  /** Trailing window, in minutes, behind rvol_window and
   * volume_concentration. A property of the screen rather than of a single
   * filter: a screen has one time context, and two filters disagreeing
   * about it would leave the displayed column ambiguous. null means "use
   * the server's default" -- read the resolved number back from
   * ScreenResponse.window_minutes. */
  window_minutes?: number | null;
}

export interface Preset {
  name: string;
  label: string;
  description: string;
  screen: Screen;
}

export interface ScreenResponse {
  session: string;
  is_latest_session: boolean;
  /** Matches before `limit` truncated them -- lets the UI say "showing 100
   * of 412" rather than leaving a full page ambiguous. */
  total_matched: number;
  tradable_size: number;
  universe_size: number;
  /** The window actually used, always resolved -- never the null a Screen
   * may have been submitted with. Column headers label themselves from
   * this. */
  window_minutes: number;
  /** Trailing window behind row.momentum_pct, in minutes. Global rather than
   * per-screen, but sent alongside so the column labels itself instead of
   * hardcoding a number that goes stale when the window changes. */
  momentum_window_minutes: number;
  rows: ScannerRow[];
  /** { field_name: { symbol: value } } for derived fields only. */
  derived: Record<string, Record<string, number | null>>;
}

/** Pushed on every poll tick to whichever socket owns this screen. Same
 * shape as ScreenResponse plus the discriminator the socket dispatches on. */
export interface ScreenUpdateMessage extends ScreenResponse {
  type: "screen_update";
}

/** Win rate / return stats for one group of picks, as bucket_analysis
 * produces them. `null` where there were no picks to average. */
export interface BucketStats {
  sample_size: number;
  win_rate: number | null;
  avg_return: number | null;
}

export interface AlphaRow {
  view: string;
  sample_size: number;
  sample_size_with_benchmark: number;
  win_rate: number;
  alpha_win_rate: number | null;
  avg_alpha: number | null;
  median_alpha: number | null;
  sufficient_sample: boolean;
}

export interface BucketRow {
  view: string;
  bucket: string;
  sample_size: number;
  win_rate: number;
  avg_return: number;
}

/** expectancy() output — win rate plus what a win and a loss are worth. */
export interface Expectancy {
  sample_size: number;
  win_rate: number;
  median: number;
  mean: number;
  avg_win: number;
  avg_loss: number;
  payoff_ratio: number | null;
}

/** Present only when a backtest was run with news catalysts. `null` deltas
 * mean one side was empty, i.e. not measurable rather than no difference. */
export interface CatalystSplit {
  with_catalyst: Expectancy | null;
  without_catalyst: Expectancy | null;
  win_rate_delta_pp: number | null;
  alpha_delta_pp: number | null;
  sufficient_sample: boolean;
  benchmark_symbol: string;
}

export interface ScreenBacktestResponse {
  resolution: BacktestResolution;
  symbol_count: number;
  symbols_with_bars: number;
  lookback_days: number;
  horizon_days: number;
  sample_size: number;
  min_sample_size: number;
  benchmark_symbol: string;
  views: string[];
  screened: boolean;
  gap_buckets: BucketRow[];
  rvol_buckets: BucketRow[];
  alpha: AlphaRow[];
  replication?: ReplicationInfo;
  window_minutes?: number;
  /** Fields this run could only replay using *today's* values — float and
   * short interest have no historical series here, so a result using them
   * carries look-ahead bias and isn't validation. Empty on a clean run. */
  look_ahead_fields: string[];
  catalyst_split?: CatalystSplit | null;
  picks: BacktestPick[];
  /** True when more picks matched than were sent. Every statistic above was
   * computed over the full set server-side; only this list is capped. */
  picks_truncated: boolean;
}

export interface BacktestPick {
  symbol: string;
  trading_date: string;
  view: string;
  /** ISO entry time. Intraday runs only — a daily pick has no time of day,
   * just the session it belongs to. */
  timestamp?: string;
  entry_pct_change: number;
  entry_gap_pct?: number | null;
  entry_rvol: number;
  entry_rvol_1h?: number | null;
  entry_dollar_volume: number;
  is_shaved_top: boolean;
  pct_change_since_entry: number;
  benchmark_pct_change_since_entry: number | null;
  alpha_vs_benchmark: number | null;
}

/** What the chart should jump to when a pick is clicked. */
export interface ChartFocus {
  symbol: string;
  /** Unix seconds -- a backtest pick's entry time, or a journal trade's
   * entry time when `trade` is set. */
  time: number;
  /** Which chart timeframe makes the pick visible: a 10:35 entry is
   * meaningless on a daily chart, and a daily pick is lost on a 5m one. */
  timeframeKey: string;
  /** Set when this focus is a closed trade (TradeJournalWidget), not a
   * backtest pick -- the chart draws "Entry"/"Exit" arrows spanning the
   * whole trade instead of a single "Pick" arrow at `time`. */
  trade?: {
    /** Unix seconds. */
    exitTime: number;
    /** Whether the trade closed profitable -- which color the exit arrow
     * gets (see CandleChart's POSITION_TARGET_COLOR/POSITION_STOP_COLOR). */
    won: boolean;
  };
}

/** 422 body when a screen filters on something daily bars can't
 * reconstruct. The backtest refuses rather than silently dropping those
 * filters and returning a number for a strategy that was never described. */
export interface BacktestRefusal {
  message: string;
  unsupported_fields: string[];
  /** Subset of unsupported_fields that intraday resolution *would* handle.
   * Non-empty means "switch resolution", not "delete these filters". */
  retry_with_intraday: string[];
  reason: string;
  backtestable_fields: string[];
}

export type BacktestResolution = "daily" | "intraday";

/** Present only on intraday runs. Every qualifying 5-minute bar is a pick, so
 * sample_size overstates independent evidence; picks_per_event is how much. */
export interface ReplicationInfo {
  sample_size: number;
  distinct_symbol_days: number;
  picks_per_event: number | null;
}

export interface FieldsResponse {
  fields: FieldSpec[];
}

export interface PresetsResponse {
  presets: Preset[];
}
