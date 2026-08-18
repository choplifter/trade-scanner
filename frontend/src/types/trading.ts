/** Alpaca's trading API, mirrored in snake_case like every other types file.
 *
 * Numeric fields arrive as **strings** -- Alpaca serialises money as decimal
 * strings to avoid float rounding, and the backend passes them through
 * untouched rather than inventing precision. Parse at the point of display
 * with `num()` below; do not retype these as `number`.
 */

export interface Account {
  account_number: string;
  status: string;
  currency: string;
  cash: string;
  equity: string;
  last_equity: string;
  buying_power: string;
  portfolio_value: string;
  long_market_value: string;
  short_market_value: string;
  daytrade_count: number | null;
  pattern_day_trader: boolean | null;
  trading_blocked: boolean;
  account_blocked: boolean;
  shorting_enabled: boolean;
}

export interface AccountResponse {
  account: Account;
  /** Whether this is the simulated account. Surfaced so the UI can label
   * itself rather than relying on the user remembering their .env. */
  paper: boolean;
  /** Whether write paths are switched on server-side (TRADING_ENABLED). */
  trading_enabled: boolean;
}

export interface Position {
  symbol: string;
  qty: string;
  side: string;
  avg_entry_price: string;
  current_price: string | null;
  market_value: string | null;
  cost_basis: string;
  unrealized_pl: string | null;
  unrealized_plpc: string | null;
  unrealized_intraday_pl: string | null;
  unrealized_intraday_plpc: string | null;
  asset_id: string;
}

export interface Order {
  id: string;
  symbol: string;
  side: string;
  order_type: string;
  qty: string | null;
  filled_qty: string | null;
  limit_price: string | null;
  stop_price: string | null;
  filled_avg_price: string | null;
  status: string;
  time_in_force: string;
  submitted_at: string | null;
  created_at: string | null;
  legs: Order[] | null;
}

export interface PositionsResponse {
  positions: Position[];
}

export interface OrdersResponse {
  orders: Order[];
  status: string;
}

/** Structured rejection body, same shape as the screener's 422 refusal. */
export interface TradingRejection {
  code: string;
  message: string;
  field: string | null;
}

/** Alpaca's decimal strings -> number, with null/empty handled once.
 * Returns null rather than NaN so callers can render an em dash. */
export function num(value: string | null | undefined): number | null {
  if (value === null || value === undefined || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}
