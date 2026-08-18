export interface ScannerRow {
  symbol: string;
  exchange: string;
  last_price: number;
  prev_close: number;
  pct_change: number;
  volume_today: number;
  avg_vol_20d: number;
  rvol: number;
  dollar_volume_today: number;
  day_high: number | null;
  day_low: number | null;
  is_hod: boolean;
  is_lod: boolean;
  float_shares: number | null;
  market_cap: number | null;
  short_interest_pct: number | null;
  country: string | null;
  company_name: string | null;
  recent_headline: string | null;
  /** % price change over just the trailing 15 minutes -- distinct from
   * pct_change (since prior close): a symbol can carry a huge pct_change
   * from earlier in the session while this reads ~0 because it's since
   * gone flat, or vice versa for a fresh late-session breakout. Null
   * until fetched, or when there isn't yet 15 minutes of bars to compare
   * against. */
  pct_change_last_15m: number | null;
  /** True when pct_change_last_15m exceeds the alarm threshold in
   * magnitude *and* the latest 1-minute candle is a marubozu (almost no
   * wick) -- see backend app.scanners.formulas.is_momentum_alert. Drives
   * the momentum-alarm overlay (see hooks/useAlarms), off by default. */
  is_momentum_alert: boolean;
  /** Volume *rate* fields, unlike rvol which is cumulative since the open
   * and only ever climbs. volume_surge is the trailing hour over the one
   * before it -- honest as a description but misleading as a filter, since
   * session volume is U-shaped and near the close it exceeds 1 for most of
   * the market. rvol_1h compares the same hour against what this symbol
   * normally trades in that clock hour, which is the one to screen on.
   * Null outside the regular session. See backend app.market_data.volume_surge. */
  volume_1h: number | null;
  volume_surge: number | null;
  rvol_1h: number | null;
  /** True when the feed hasn't confirmed this price via a real trade/bar
   * recently -- the row still ranks normally, but the price shown may be
   * older than it looks. */
  is_stale: boolean;
  /** True when rvol > 15x -- historically a fade-risk signal (lower win
   * rate) rather than an extra-bullish one; ranking already discounts for
   * it, this flags it directly too. */
  is_fade_risk: boolean;
  updated_at: string;
}

export interface ScannerUpdateMessage {
  type: "scanner_update";
  scanner: string;
  session: string;
  /** True when there's nothing live (e.g. markets closed) and these rows
   * are the most recently completed session's real data instead. */
  is_latest_session: boolean;
  rows: ScannerRow[];
}

export interface Bar {
  t: string;
  o: number;
  h: number;
  l: number;
  c: number;
  v: number;
}

export interface ChartBarMessage {
  type: "bar";
  symbol: string;
  bar: Bar;
  vwap: number | null;
  vwap_premarket: number | null;
}

export interface ChartErrorMessage {
  type: "error";
  symbol: string;
  message: string;
}

export type ChartSocketMessage = ChartBarMessage | ChartErrorMessage;

/** One named indicator's computed result -- "level" is a static horizontal
 * reference price per sub-series (e.g. {High, Low}), "series" is a
 * value-per-bar line (unused by any built-in indicator yet, but the shape
 * both frontends draw generically either way). Backed by whatever .py files
 * currently exist in backend/app/indicators/ -- this list is not a fixed
 * set of names. */
export interface IndicatorResult {
  name: string;
  kind: "level" | "series";
  series: Record<string, number | null | { t: string; value: number | null }[]>;
  colors: Record<string, string>;
}

export interface SymbolBarsResponse {
  symbol: string;
  bars: Bar[];
  vwap: (number | null)[];
  vwap_premarket: (number | null)[];
  indicators: IndicatorResult[];
}

export type MarketSession = "premarket" | "regular" | "afterhours" | "closed";
