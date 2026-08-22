/** One point in time on the price series, as opposed to a price level that
 * spans the whole chart. A horizontal line answers "at what price"; this
 * answers "at which bar", which is the question an entry actually poses. */
export interface IndicatorMarker {
  /** Bar time, seconds since the epoch. */
  time: number;
  position: "aboveBar" | "belowBar" | "inBar";
  shape: "arrowUp" | "arrowDown" | "circle" | "square";
  text: string;
}

export interface StrategySignal {
  strategy: string;
  /** "long" or "short" -- unlike the momentum alarm, these rules work from
   * either side of a level. */
  side: string;
  entry_price: number;
  stop_price: number;
  target_price: number;
  reason: string;
  /** When the setup fired. This marks the last setup of the session, not one
   * firing this instant -- a strict now-flag would fall between the
   * scanner's refreshes more often than not. */
  fired_at: string;
}

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
  /** The company's sector, from the same FMP profile as country/company_name
   * and populated for ranked rows only. Drives the sector-attribution page;
   * deliberately not a screener field, since a filter on something null for
   * most of the universe would silently match nothing. */
  sector: string | null;
  company_name: string | null;
  recent_headline: string | null;
  /** % price change over just the momentum window -- distinct from
   * pct_change (since prior close): a symbol can carry a huge pct_change
   * from earlier in the session while this reads ~0 because it's since
   * gone flat, or vice versa for a fresh late-session breakout. Null
   * until fetched, or when there isn't yet a full window of bars to compare
   * against. */
  momentum_pct: number | null;
  /** True when momentum_pct exceeds the alarm threshold in
   * magnitude *and* the latest 1-minute candle is a marubozu (almost no
   * wick) -- see backend app.scanners.formulas.is_momentum_alert. Drives
   * the momentum-alarm overlay (see hooks/useAlarms), off by default. */
  is_momentum_alert: boolean;
  /** Strategy scripts firing on this row's latest bar -- see backend
   * app/strategies/. Empty for almost every row. A list rather than one flag
   * per strategy, so adding a script does not change this contract.
   *
   * The backend computes these from the scanner's cached 5-minute bars,
   * whose level sources are narrower than the chart's -- so treat a marker
   * as "look at this", and the chart as the authority on the prices. */
  strategy_signals: StrategySignal[];
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
  /** True when the broker will let this symbol be sold short. Known for the
   * whole universe (it comes off the Alpaca asset record), and false also
   * covers "unknown" -- so a true here is a positive signal while a false is
   * only the absence of one. The broker's static flag, not a live locate:
   * a borrow can still fail when the order is placed. */
  shortable: boolean;
  updated_at: string;
}

export interface ScannerUpdateMessage {
  type: "scanner_update";
  scanner: string;
  session: string;
  /** True when there's nothing live (e.g. markets closed) and these rows
   * are the most recently completed session's real data instead. */
  is_latest_session: boolean;
  /** Trailing window behind row.rvol_1h, in minutes -- so the column can
   * label itself instead of hardcoding "1h". */
  window_minutes: number;
  /** Trailing window behind row.momentum_pct, in minutes. Global rather than
   * per-screen, but sent alongside so the column labels itself instead of
   * hardcoding a number that goes stale when the window changes. */
  momentum_window_minutes: number;
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
/** How one indicator's lines are drawn. Declared per indicator file
 * (see STYLE in the backend's indicator loader) so weight and dash pattern
 * can distinguish one kind of line from another; either key may be absent,
 * and an absent STYLE leaves the default for the indicator's kind. */
export interface IndicatorStyle {
  width?: number;
  dash?: "solid" | "dotted" | "dashed" | "large-dashed" | "sparse-dotted";
}

export interface IndicatorResult {
  name: string;
  kind: "level" | "series" | "marker" | "error";
  /** What each sub-series carries depends on `kind`: a price for "level", a
   * point list for "series", a marker list for "marker". The union is
   * narrowed at the render site by checking `kind` first. */
  series: Record<
    string,
    number | null | { t: string; value: number | null }[] | IndicatorMarker[]
  >;
  colors: Record<string, string>;
  style?: IndicatorStyle;
  /** Set when the file could not be loaded or computed. Carried instead of
   * dropping the entry: an indicator that vanishes draws nothing, which on a
   * chart looks exactly like one that found nothing. */
  error?: string;
}

/** `kind` decides which shape a sub-series carries, but TypeScript cannot
 * narrow a Record's values from a sibling field -- so the check has to look at
 * the value. Guards rather than casts: a cast would keep compiling if the
 * backend ever changed the payload shape, which is exactly the change that
 * should stop compiling. */
export function isPointSeries(
  value: unknown,
): value is { t: string; value: number | null }[] {
  return Array.isArray(value) && (value.length === 0 || "t" in value[0]);
}

export function isMarkerSeries(value: unknown): value is IndicatorMarker[] {
  return Array.isArray(value) && (value.length === 0 || "time" in value[0]);
}

export interface SymbolBarsResponse {
  symbol: string;
  bars: Bar[];
  vwap: (number | null)[];
  vwap_premarket: (number | null)[];
  indicators: IndicatorResult[];
}

export type MarketSession = "premarket" | "regular" | "afterhours" | "closed";
