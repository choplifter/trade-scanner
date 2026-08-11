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
  /** True when the feed hasn't confirmed this price via a real trade/bar
   * recently -- the row still ranks normally, but the price shown may be
   * older than it looks. */
  is_stale: boolean;
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
}

export interface ChartErrorMessage {
  type: "error";
  symbol: string;
  message: string;
}

export type ChartSocketMessage = ChartBarMessage | ChartErrorMessage;

export interface SymbolBarsResponse {
  symbol: string;
  bars: Bar[];
  vwap: (number | null)[];
}

export type MarketSession = "premarket" | "regular" | "afterhours" | "closed";
