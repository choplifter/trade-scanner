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
