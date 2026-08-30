import type { ScannerRow, SymbolBarsResponse } from "../types/alpaca";
import type { MarketConditionsResponse } from "../types/marketConditions";
import type { ScannerBenchmarkResponse } from "../types/scannerBenchmark";
import type { ScannerHistoryResponse } from "../types/scannerHistory";
import type {
  BacktestRefusal,
  BacktestResolution,
  FieldsResponse,
  PresetsResponse,
  Screen,
  ScreenBacktestResponse,
} from "../types/screener";
import type { SymbolInfoResponse } from "../types/symbolInfo";
import type { SymbolSuggestion, WatchlistQuotes } from "../types/watchlist";
import type {
  AccountResponse,
  BalanceRange,
  OrderPreview,
  OrderTicketRequest,
  Order,
  OrdersResponse,
  PortfolioHistoryResponse,
  PositionsResponse,
  TradesRange,
  TradesResponse,
  TradingRejection,
} from "../types/trading";
import type { TradeIdeasPerformanceResponse, TradeIdeasResponse } from "../types/tradeIdeas";

const API_BASE = "/api";

async function extractErrorMessage(res: Response, fallback: string): Promise<string> {
  try {
    const body = (await res.json()) as { detail?: string };
    return body.detail ?? fallback;
  } catch {
    return fallback;
  }
}

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`);
  if (!res.ok) {
    throw new Error(await extractErrorMessage(res, `GET ${path} failed: ${res.status}`));
  }
  return (await res.json()) as T;
}

async function postJson<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    ...(body === undefined
      ? {}
      : { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }),
  });
  if (!res.ok) {
    throw new Error(await extractErrorMessage(res, `POST ${path} failed: ${res.status}`));
  }
  return (await res.json()) as T;
}

export interface ScannerResponse {
  scanner: string;
  session: string;
  is_latest_session: boolean;
  window_minutes: number;
  /** The trailing window row.momentum_pct was computed over. Global, unlike
   * window_minutes above, but sent so the column can label itself instead of
   * hardcoding a number that goes stale the moment the window changes. */
  momentum_window_minutes: number;
  rows: ScannerRow[];
}

export function getScanner(name: string): Promise<ScannerResponse> {
  return getJson<ScannerResponse>(`/scanners/${name}`);
}

export function getScannerBenchmarkPerformance(): Promise<ScannerBenchmarkResponse> {
  return getJson<ScannerBenchmarkResponse>("/scanners/benchmark-performance");
}

export function getScannerHistoryPerformance(days = 7): Promise<ScannerHistoryResponse> {
  return getJson<ScannerHistoryResponse>(`/scanners/history/performance?days=${days}`);
}

export function getSymbolBars(symbol: string, timeframe = "1Min"): Promise<SymbolBarsResponse> {
  return getJson<SymbolBarsResponse>(`/symbols/${symbol}/bars?timeframe=${encodeURIComponent(timeframe)}`);
}

export function getSymbolInfo(symbol: string): Promise<SymbolInfoResponse> {
  return getJson<SymbolInfoResponse>(`/symbols/${symbol}/info`);
}

/** Prefix matches against the full active-equity list (ETFs and anything
 * outside the scanner's price/volume band included -- see
 * list_active_equity_symbols) -- used for watchlist add-symbol suggestions
 * only, never as validation: addSymbol accepts any ticker-shaped string
 * regardless of whether it ever shows up here. */
export function searchSymbols(query: string): Promise<{ matches: SymbolSuggestion[] }> {
  return getJson<{ matches: SymbolSuggestion[] }>(`/symbols/search?q=${encodeURIComponent(query)}`);
}

/** Last price / % change for an arbitrary symbol list, polled by the
 * watchlist panel. Works for any symbol regardless of universe membership --
 * see routers/watchlist.py for why this can't just reuse the chart feed. */
export function getWatchlistQuotes(symbols: string[]): Promise<WatchlistQuotes> {
  if (symbols.length === 0) return Promise.resolve({});
  return getJson<WatchlistQuotes>(`/watchlist/quotes?symbols=${encodeURIComponent(symbols.join(","))}`);
}

export interface SessionResponse {
  session: string;
  checked_at: string;
}

export function getSession(): Promise<SessionResponse> {
  return getJson<SessionResponse>("/meta/session");
}

export function getMarketConditions(): Promise<MarketConditionsResponse> {
  return getJson<MarketConditionsResponse>("/meta/market-conditions");
}

export interface StrategySwitch {
  name: string;
  filename: string;
  /** Filename minus .py -- the key the toggle endpoint speaks. */
  stem: string;
  enabled: boolean;
}

export interface StrategiesResponse {
  strategies: StrategySwitch[];
  /** Files that failed to load. Shown, not hidden: a strategy that failed
   * to load looks exactly like a quiet market. */
  errors: { filename: string; error: string }[];
  /** The break rules' measured-move fallback: with no level ahead of an
   * entry, aim at a constructed 2R target instead of declining the trade.
   * One shared setting -- four rules read it. */
  measured_move_target: boolean;
  /** The opening range length every ORB-family rule and the chart's box
   * read. The valid values come along so the UI never hardcodes them. */
  opening_range_minutes: number;
  opening_range_choices: number[];
}

export function getStrategies(): Promise<StrategiesResponse> {
  return getJson<StrategiesResponse>("/strategies");
}

/** Flip one strategy's switch. Returns the refreshed listing, so the caller
 * can render the server's view instead of an optimistic one. */
export function setStrategyEnabled(stem: string, enabled: boolean): Promise<StrategiesResponse> {
  return postJson<StrategiesResponse>(`/strategies/${encodeURIComponent(stem)}`, { enabled });
}

export function setMeasuredMoveTarget(enabled: boolean): Promise<StrategiesResponse> {
  return postJson<StrategiesResponse>("/strategies/settings/measured-move", { enabled });
}

export function setOpeningRangeMinutes(minutes: number): Promise<StrategiesResponse> {
  return postJson<StrategiesResponse>("/strategies/settings/opening-range", { minutes });
}

export function postTradeIdeas(): Promise<TradeIdeasResponse> {
  return postJson<TradeIdeasResponse>("/trade-ideas");
}

export function getTradeIdeasPerformance(): Promise<TradeIdeasPerformanceResponse> {
  return getJson<TradeIdeasPerformanceResponse>("/trade-ideas/performance");
}

/** The screenable field registry. Fetched once and used to build every
 * field picker, operator list and column in the screener UI -- nothing about
 * which fields exist lives in the frontend. */
export function getScreenerFields(): Promise<FieldsResponse> {
  return getJson<FieldsResponse>("/screener/fields");
}

/** Built-in screens, including the three that used to be fixed views.
 * Full filter specs, so loading one lets the user then edit it. */
export function getScreenerPresets(): Promise<PresetsResponse> {
  return getJson<PresetsResponse>("/screener/presets");
}

// No runScreen() helper here on purpose: the app subscribes to a live screen
// over the scanner websocket (see api/ws.ts subscribeScreen) rather than
// polling. POST /api/screener/run still exists server-side as a one-shot
// scriptable endpoint, it just has no client caller.

/** Thrown when the backtest refuses a screen it can't replay on daily bars.
 * Carries the offending field names so the UI can name them rather than
 * showing a generic failure. */
export class BacktestRefusedError extends Error {
  constructor(readonly detail: BacktestRefusal) {
    super(detail.message);
    this.name = "BacktestRefusedError";
  }
}

export async function backtestScreen(
  screen: Screen,
  options: {
    lookback_days?: number;
    horizon_days?: number;
    max_symbols?: number;
    resolution?: BacktestResolution;
    with_catalysts?: boolean;
  } = {},
): Promise<ScreenBacktestResponse> {
  const res = await fetch(`${API_BASE}/screener/backtest`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ screen, ...options }),
  });
  if (res.status === 422) {
    const body = (await res.json()) as { detail: BacktestRefusal };
    throw new BacktestRefusedError(body.detail);
  }
  if (!res.ok) {
    throw new Error(await extractErrorMessage(res, `Backtest failed: ${res.status}`));
  }
  return (await res.json()) as ScreenBacktestResponse;
}

/** Connected Alpaca account, plus which mode the backend is in. Read-only:
 * available whenever credentials exist, regardless of TRADING_ENABLED. */
export function getAccount(): Promise<AccountResponse> {
  return getJson<AccountResponse>("/trading/account");
}

export function getPositions(): Promise<PositionsResponse> {
  return getJson<PositionsResponse>("/trading/positions");
}

/** Working orders by default; "all" or "closed" for history. */
export function getOrders(status = "open"): Promise<OrdersResponse> {
  return getJson<OrdersResponse>(`/trading/orders?status=${encodeURIComponent(status)}`);
}

/** Closed round trips with realized P&L, newest first, narrowed to a
 * calendar period. Read-only; each call also has the backend record any
 * trip that closed since the last one. */
export function getTrades(range: TradesRange = "all"): Promise<TradesResponse> {
  return getJson<TradesResponse>(`/trading/trades?range=${encodeURIComponent(range)}`);
}

/** The account equity curve for one range. Read-only, like getAccount. */
export function getPortfolioHistory(range: BalanceRange): Promise<PortfolioHistoryResponse> {
  return getJson<PortfolioHistoryResponse>(
    `/trading/portfolio-history?range=${encodeURIComponent(range)}`,
  );
}

/** Thrown when the backend refuses a ticket -- a stop on the wrong side, a
 * size past a ceiling, trading switched off. Carries the structured reason
 * so the ticket can point at the offending field instead of showing a
 * generic failure. Same pattern as BacktestRefusedError above. */
export class OrderRejectedError extends Error {
  constructor(readonly detail: TradingRejection) {
    super(detail.message);
    this.name = "OrderRejectedError";
  }
}

/** Today's high for `symbol` -- the trigger price the breakout-entry hotkey
 * sizes off. Returns null rather than throwing when unavailable (e.g. no
 * quote data yet), matching how the ticket already treats a missing
 * reference price -- a hotkey guard, not a hard failure. */
export async function dayHigh(symbol: string): Promise<number | null> {
  const res = await fetch(`${API_BASE}/trading/day-high/${encodeURIComponent(symbol)}`);
  if (!res.ok) return null;
  const body = (await res.json()) as { day_high: number | null };
  return body.day_high;
}

/** Last price for `symbol`, independent of any ticket -- what the Stop/
 * Target auto-suggestion sizes off before a ticket exists to preview. Same
 * null-on-failure shape as dayHigh, for the same reason. */
export async function referencePrice(symbol: string): Promise<number | null> {
  const res = await fetch(`${API_BASE}/trading/reference-price/${encodeURIComponent(symbol)}`);
  if (!res.ok) return null;
  const body = (await res.json()) as { price: number | null };
  return body.price;
}

/** Size and price a ticket without placing anything. */
export async function previewOrder(ticket: OrderTicketRequest): Promise<OrderPreview> {
  const res = await fetch(`${API_BASE}/trading/orders/preview`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(ticket),
  });
  if (res.status === 422) {
    const body = (await res.json()) as { detail: TradingRejection };
    throw new OrderRejectedError(body.detail);
  }
  if (!res.ok) {
    throw new Error(await extractErrorMessage(res, `Preview failed: ${res.status}`));
  }
  return (await res.json()) as OrderPreview;
}

/** Place an order. Refusals -- switched off, live account, bad stop, past a
 * ceiling, or the broker's own -- all arrive as a typed 422 so the ticket
 * renders them through one path. */
export async function submitOrder(ticket: OrderTicketRequest): Promise<{ order: Order }> {
  const res = await fetch(`${API_BASE}/trading/orders`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(ticket),
  });
  if (res.status === 422) {
    const body = (await res.json()) as { detail: TradingRejection };
    throw new OrderRejectedError(body.detail);
  }
  if (!res.ok) {
    throw new Error(await extractErrorMessage(res, `Order failed: ${res.status}`));
  }
  return (await res.json()) as { order: Order };
}

async function deleteJson<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { method: "DELETE" });
  if (res.status === 422) {
    const body = (await res.json()) as { detail: TradingRejection };
    throw new OrderRejectedError(body.detail);
  }
  if (!res.ok) {
    throw new Error(await extractErrorMessage(res, `DELETE ${path} failed: ${res.status}`));
  }
  return (await res.json()) as T;
}

async function patchJson<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (res.status === 422) {
    const detail = (await res.json()) as { detail: TradingRejection };
    throw new OrderRejectedError(detail.detail);
  }
  if (!res.ok) {
    throw new Error(await extractErrorMessage(res, `PATCH ${path} failed: ${res.status}`));
  }
  return (await res.json()) as T;
}

export function cancelOrder(orderId: string): Promise<{ cancelled: string }> {
  return deleteJson<{ cancelled: string }>(`/trading/orders/${encodeURIComponent(orderId)}`);
}

/** Move a working stop order to a new price. The symbol rides along as a
 * cross-check -- the server refuses if the id and the symbol disagree. */
export function replaceStop(
  orderId: string,
  symbol: string,
  stopPrice: number,
): Promise<{ order: Order }> {
  return patchJson<{ order: Order }>(`/trading/orders/${encodeURIComponent(orderId)}`, {
    symbol,
    stop_price: stopPrice,
  });
}

/** Move a working take-profit order to a new price. Same cross-check
 * convention as replaceStop, on the distinct /target path the take-profit
 * leg needs (see OrderService.replace_target). */
export function replaceTarget(
  orderId: string,
  symbol: string,
  limitPrice: number,
): Promise<{ order: Order }> {
  return patchJson<{ order: Order }>(`/trading/orders/${encodeURIComponent(orderId)}/target`, {
    symbol,
    limit_price: limitPrice,
  });
}

/** The close response, which for a partial close also reports what happened
 * to the position's exits -- see OrderService.close_position on stop_lost. */
export interface CloseResult {
  order: Order & {
    cancelled_orders?: string[];
    rearmed_orders?: Order[];
    stop_lost?: boolean;
  };
}

export function closePosition(symbol: string, qty?: number): Promise<CloseResult> {
  const suffix = qty != null ? `?qty=${encodeURIComponent(qty)}` : "";
  return deleteJson<CloseResult>(`/trading/positions/${encodeURIComponent(symbol)}${suffix}`);
}
