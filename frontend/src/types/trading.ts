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

export type TradingAccount = "paper" | "live";

export interface AccountLimits {
  account: TradingAccount;
  max_order_qty: number;
  max_order_notional: number;
  max_order_notional_pct: number;
  max_option_contracts: number;
}

export interface AccountResponse {
  account: Account;
  /** Which Alpaca account answered: the paper one, or the real one behind
   * the /live prefix. */
  trading_account: TradingAccount;
  /** Whether this is the simulated account. Surfaced so the UI can label
   * itself rather than relying on the user remembering their .env. */
  paper: boolean;
  /** A live key pair is configured server-side. */
  live_available: boolean;
  /** TRADING_ALLOW_LIVE is on. Both must hold before Live is offered. */
  live_allowed: boolean;
  /** The fat-finger ceilings for this account. */
  limits: AccountLimits;
  /** Whether write paths are switched on server-side (TRADING_ENABLED). */
  trading_enabled: boolean;
  /** Prefills the ticket's risk field. */
  default_risk_pct: number;
}

export interface Position {
  symbol: string;
  /** "us_equity" or "us_option"; option legs are shown by the Options widget. */
  asset_class?: string;
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
  /** Null on a multi-leg (options spread) parent -- see `legs`. */
  symbol: string | null;
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
  filled_at: string | null;
  legs: Order[] | null;
}

export interface PositionsResponse {
  positions: Position[];
}

export interface OrdersResponse {
  orders: Order[];
  status: string;
}

/** One closed round trip: opened from flat, closed back to flat. Computed
 * server-side from fills and persisted there, since the broker keeps no
 * such thing. Numbers, not strings -- these are ours, not Alpaca's. */
export interface Trade {
  id: string;
  symbol: string;
  side: "long" | "short";
  opened_at: string;
  closed_at: string;
  qty: number;
  /** Shares per unit of qty: 100 for an option contract, 1 for a stock;
   * pnl and R already include it. */
  multiplier?: number;
  entry_avg: number;
  exit_avg: number;
  pnl: number;
  pnl_pct: number | null;
  /** The stop-loss leg the entry was placed with; null for a naked entry. */
  initial_stop: number | null;
  risk_per_share: number | null;
  /** pnl / (risk_per_share * qty): the unit the strategy backtests report
   * expectancy in. Null when there was no initial stop to measure against. */
  r_multiple: number | null;
  entry_order_id: string;
  exit_order_ids: string[];
  fill_count: number;
}

export interface TradeSummary {
  count: number;
  wins: number;
  losses: number;
  /** Percent of decided (non-flat) trades that won; null with none. */
  win_rate: number | null;
  total_pnl: number;
  avg_win: number | null;
  avg_loss: number | null;
  profit_factor: number | null;
  /** How many trades had an R to measure. */
  r_count: number;
  avg_r: number | null;
  total_r: number | null;
}

/** Calendar periods, in ET: today's session, this week from Monday, this
 * month from the 1st. Not rolling windows. */
export type TradesRange = "day" | "week" | "month" | "all";

/** One ET trading date's subtotal within the requested period. */
export interface TradeBucket {
  /** YYYY-MM-DD in ET. */
  date: string;
  count: number;
  wins: number;
  losses: number;
  pnl: number;
  /** Running total through this date, oldest first. */
  cumulative_pnl: number;
}

export interface TradesResponse {
  range: TradesRange;
  /** ISO start of the period, or null for "all". */
  period_start: string | null;
  /** Only the trades closed within the period. */
  trades: Trade[];
  /** Summary over those trades. */
  summary: TradeSummary;
  /** Per-day subtotals over those trades, oldest first. */
  buckets: TradeBucket[];
  /** Symbols with fills whose position is still open -- not trades yet. */
  open_symbols: string[];
}

/** The ranges the balance curve offers. The matching Alpaca period and
 * timeframe are chosen server-side -- the valid timeframe depends on the
 * period's length, so the pair is not the UI's to assemble. */
export type BalanceRange = "1D" | "1W" | "1M" | "3M" | "1Y" | "ALL";

/** One sample on the equity curve.
 *
 * Unlike Account/Position/Order above, these really are numbers: the values
 * come from Alpaca's portfolio-history arrays rather than its decimal-string
 * money fields, and the backend normalises them (including converting
 * profit_loss_pct from a fraction to a percentage) before they get here. So
 * no num() at the point of display.
 */
export interface BalancePoint {
  /** Unix seconds, as lightweight-charts wants them. */
  t: number;
  equity: number;
  profit_loss: number | null;
  profit_loss_pct: number | null;
}

export interface PortfolioHistoryResponse {
  range: BalanceRange;
  /** Alpaca's sampling interval for this range -- "5Min", "1D", etc. Shown
   * so the curve's resolution is legible rather than guessed from density. */
  timeframe: string;
  /** Already trimmed of the pre-inception zero padding Alpaca pads short
   * account histories with, and extended to the live equity on daily
   * ranges. See OrderService.portfolio_history. */
  points: BalancePoint[];
  start_equity: number | null;
  end_equity: number | null;
  /** End minus start across the plotted window -- the period's P&L. */
  change: number | null;
  change_pct: number | null;
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

/** The exit orders protecting one position, if any exist.
 *
 * Alpaca's position object carries no take-profit or stop-loss: they are
 * ordinary open orders on the same symbol, which is why the two tables used
 * to sit side by side with no way to see that a position had no stop at
 * all. This is the join.
 */
export interface PositionExits {
  takeProfit: number | null;
  stopLoss: number | null;
  /** The stop order's own id -- what the move-stop endpoint takes. The
   * client is where this join lives, so the client supplies the id and the
   * server only cross-checks it (see OrderService.replace_stop). */
  stopOrderId: string | null;
  /** Same as stopOrderId, for the take-profit leg -- what the move-target
   * endpoint takes (see OrderService.replace_target). */
  targetOrderId: string | null;
}

/** Orders flattened so a bracket parent's legs are matched too -- Alpaca may
 * return the two exits nested under the entry order rather than as
 * top-level orders, depending on how the bracket was placed. */
function withLegs(orders: Order[]): Order[] {
  return orders.flatMap((o) => [o, ...(o.legs ?? [])]);
}

export function exitsForPosition(position: Position, orders: Order[]): PositionExits {
  // An exit closes the position, so it sits on the opposite side. Filtering
  // on this is what stops a *pyramiding* order -- another buy on a symbol
  // already long -- being read as a take-profit.
  const closingSide = position.side === "long" ? "sell" : "buy";
  const candidates = withLegs(orders).filter(
    (o) => o.symbol === position.symbol && o.side === closingSide,
  );

  // Classified by order_type rather than by comparing prices to the entry:
  // a stop_limit carries both a stop and a limit price, so "whichever is
  // above" would report one order as both exits.
  const stop = candidates.find((o) => o.order_type.includes("stop"));
  const limit = candidates.find((o) => o.order_type === "limit");

  return {
    takeProfit: limit ? num(limit.limit_price) : null,
    stopLoss: stop ? num(stop.stop_price) : null,
    stopOrderId: stop ? stop.id : null,
    targetOrderId: limit ? limit.id : null,
  };
}

/** What the ticket sends. Exactly one of qty / risk, and exactly one of
 * risk_amount / risk_pct_of_equity -- the backend rejects both-or-neither
 * rather than picking for you. */
export interface RiskSizingRequest {
  stop_price: number;
  risk_amount?: number;
  risk_pct_of_equity?: number;
}

/** Entry types. `stop` / `stop_limit` are breakout entries -- the order
 * rests until price trades through `stop_price`, then goes in as a market /
 * limit order. Distinct from the stop-loss leg, which protects the position
 * the entry opens. A *limit* on the far side of the market is not a resting
 * order: a buy limit means "this price or lower" and fills at once. */
export type EntryOrderType = "market" | "limit" | "stop" | "stop_limit";

export interface OrderTicketRequest {
  symbol: string;
  side: "buy" | "sell";
  order_type: EntryOrderType;
  time_in_force?: "day" | "gtc";
  qty?: number;
  risk?: RiskSizingRequest;
  limit_price?: number;
  /** The trigger of a stop / stop_limit entry. */
  stop_price?: number;
  take_profit_price?: number;
  stop_loss_price?: number;
  client_order_id?: string;
}

/** The ticket priced and sized, as the broker would receive it. */
export interface ResolvedOrder {
  symbol: string;
  side: string;
  order_type: string;
  /** What the order will actually be sent as. Derived server-side when the
   * ticket does not pin it: a ticket carrying protective legs defaults to
   * gtc, because a day order's legs expire at the close while the position
   * they protect does not. */
  time_in_force: string;
  order_class: string;
  qty: number;
  entry_reference: number;
  notional: number;
  limit_price: number | null;
  /** Entry trigger of a stop / stop_limit order; null otherwise. */
  stop_price: number | null;
  take_profit_price: number | null;
  stop_loss_price: number | null;
  risk_amount: number | null;
  risk_per_share: number | null;
  risk_pct_of_equity: number | null;
  /** Not refusals, but things the server wants said before the confirm --
   * chiefly a limit that will fill on arrival instead of resting. */
  warnings: string[];
}

export interface OrderPreview {
  order: ResolvedOrder;
  /** Server-side answer to "would a submit be accepted right now" -- driven
   * by TRADING_ENABLED and the paper check, never inferred client-side. */
  can_submit: boolean;
  limits: {
    account: TradingAccount;
    max_order_qty: number;
    max_order_notional: number;
    default_risk_pct: number;
  };
}
