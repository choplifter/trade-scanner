import type { ScannerRow, SymbolBarsResponse } from "../types/alpaca";
import type { GexPlanResponse, GexResponse } from "../types/gex";
import type { JournalEntry, JournalResponse } from "../types/journal";
import type { MarketConditionsResponse } from "../types/marketConditions";
import type { NewsFeedItem } from "../types/newsFeed";
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
import { tradingPath } from "./tradingMode";
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

/** Fires when any call below gets a 401 -- the session cookie the browser
 * sent was missing or no longer validated server-side, regardless of why
 * (expired, a `.env` secret rotation, a proxy dropping it). Widgets don't
 * each detect this on their own -- polling loops swallow their own errors
 * and just retry -- so without a shared signal the dashboard was staying
 * on a half-populated, silently-broken screen instead of prompting a fresh
 * login. useAuth subscribes and clears `user`, which sends App back to
 * LoginPage the same way an explicit logout does. */
type UnauthorizedListener = () => void;
const unauthorizedListeners = new Set<UnauthorizedListener>();

export function onUnauthorized(listener: UnauthorizedListener): () => void {
  unauthorizedListeners.add(listener);
  return () => unauthorizedListeners.delete(listener);
}

function checkUnauthorized(res: Response): void {
  if (res.status === 401) {
    unauthorizedListeners.forEach((fn) => fn());
  }
}

async function extractErrorMessage(res: Response, fallback: string): Promise<string> {
  try {
    const body = (await res.json()) as { detail?: string };
    return body.detail ?? fallback;
  } catch {
    return fallback;
  }
}

export async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { credentials: "include" });
  checkUnauthorized(res);
  if (!res.ok) {
    throw new Error(await extractErrorMessage(res, `GET ${path} failed: ${res.status}`));
  }
  return (await res.json()) as T;
}

export async function postJson<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    credentials: "include",
    ...(body === undefined
      ? {}
      : { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }),
  });
  checkUnauthorized(res);
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

/** The live cross-symbol news feed's current buffer, newest first --
 * seeds a freshly mounted NewsFeedWidget; new items after that arrive
 * over /ws/news-feed instead (see api/ws.ts's newsFeedSocket). */
export function getRecentNewsFeed(limit = 50): Promise<{ items: NewsFeedItem[] }> {
  return getJson<{ items: NewsFeedItem[] }>(`/news-feed/recent?limit=${limit}`);
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

/** The logged-in user's watchlist symbol list -- per-user backend state
 * (see app.watchlist.store.WatchlistStore), seeded from the same default
 * list on first use. */
export function getWatchlistSymbols(): Promise<{ symbols: string[] }> {
  return getJson<{ symbols: string[] }>("/watchlist/symbols");
}

export function addWatchlistSymbol(symbol: string): Promise<{ symbols: string[] }> {
  return postJson<{ symbols: string[] }>("/watchlist/symbols", { symbol });
}

export function removeWatchlistSymbol(symbol: string): Promise<{ symbols: string[] }> {
  return deleteJson<{ symbols: string[] }>(`/watchlist/symbols/${encodeURIComponent(symbol)}`);
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

export function getGex(): Promise<GexResponse> {
  return getJson<GexResponse>("/meta/gex");
}

export function getGexPlan(): Promise<GexPlanResponse> {
  return getJson<GexPlanResponse>("/meta/gex-plan");
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
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ screen, ...options }),
  });
  checkUnauthorized(res);
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
 * available whenever credentials exist, regardless of TRADING_ENABLED.
 * Every trading-specific function below routes through tradingPath(), which
 * transparently swaps this for Simulation Mode's local order book -- see
 * api/tradingMode.ts. */
export function getAccount(): Promise<AccountResponse> {
  return getJson<AccountResponse>(tradingPath("/trading/account"));
}

export function getPositions(): Promise<PositionsResponse> {
  return getJson<PositionsResponse>(tradingPath("/trading/positions"));
}

/** Working orders by default; "all" or "closed" for history. */
export function getOrders(status = "open"): Promise<OrdersResponse> {
  return getJson<OrdersResponse>(tradingPath(`/trading/orders?status=${encodeURIComponent(status)}`));
}

/** Closed round trips with realized P&L, newest first, narrowed to a
 * calendar period. Read-only; each call also has the backend record any
 * trip that closed since the last one. */
export function getTrades(range: TradesRange = "all"): Promise<TradesResponse> {
  return getJson<TradesResponse>(tradingPath(`/trading/trades?range=${encodeURIComponent(range)}`));
}

/** This user's notes on closed trades -- see routers/trading.py's /journal.
 * Not routed through tradingPath(): trade_id alone is already unambiguous
 * across real and Simulation Mode trades (see JournalStore's docstring), so
 * the journal endpoints don't distinguish between the two trading modes. */
export function getJournalEntries(): Promise<JournalResponse> {
  return getJson<JournalResponse>("/trading/journal");
}

export function saveJournalEntry(
  tradeId: string,
  body: { note: string; rating: number | null; tags: string[] },
): Promise<{ entry: JournalEntry }> {
  return postJson<{ entry: JournalEntry }>(`/trading/journal/${encodeURIComponent(tradeId)}`, body);
}

/** The account equity curve for one range. Read-only, like getAccount. */
export function getPortfolioHistory(range: BalanceRange): Promise<PortfolioHistoryResponse> {
  return getJson<PortfolioHistoryResponse>(
    tradingPath(`/trading/portfolio-history?range=${encodeURIComponent(range)}`),
  );
}

/** Wipes Simulation Mode's positions/orders/trades and reseeds cash --
 * see routers/trading_sim.py's /reset. No real-trading equivalent, and
 * deliberately not routed through tradingPath(): there's nothing to rewrite
 * from, and it should only ever be called while simulation mode is active. */
export function resetSimAccount(): Promise<AccountResponse> {
  return postJson<AccountResponse>("/trading/sim/reset");
}

/** Thrown when the backend refuses a ticket -- a stop on the wrong side, a
 * size past a ceiling, trading switched off. Carries the structured reason
 * so the ticket can point at the offending field instead of showing a
 * generic failure. Same pattern as BacktestRefusedError above. */
/** The typed LIVE from a live-mode dialog, as the header the backend
 * checks (app/trading/guards.py). Empty outside Live mode. */
function liveConfirmHeaders(confirm?: string): Record<string, string> {
  return confirm ? { "X-Live-Confirm": confirm } : {};
}

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
  const res = await fetch(`${API_BASE}${tradingPath(`/trading/day-high/${encodeURIComponent(symbol)}`)}`, {
    credentials: "include",
  });
  checkUnauthorized(res);
  if (!res.ok) return null;
  const body = (await res.json()) as { day_high: number | null };
  return body.day_high;
}

/** Last price for `symbol`, independent of any ticket -- what the Stop/
 * Target auto-suggestion sizes off before a ticket exists to preview. Same
 * null-on-failure shape as dayHigh, for the same reason. */
export async function referencePrice(symbol: string): Promise<number | null> {
  const res = await fetch(
    `${API_BASE}${tradingPath(`/trading/reference-price/${encodeURIComponent(symbol)}`)}`,
    { credentials: "include" },
  );
  checkUnauthorized(res);
  if (!res.ok) return null;
  const body = (await res.json()) as { price: number | null };
  return body.price;
}

/** Size and price a ticket without placing anything. */
export async function previewOrder(ticket: OrderTicketRequest): Promise<OrderPreview> {
  const res = await fetch(`${API_BASE}${tradingPath("/trading/orders/preview")}`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(ticket),
  });
  checkUnauthorized(res);
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
export async function submitOrder(
  ticket: OrderTicketRequest,
  confirm?: string,
): Promise<{ order: Order }> {
  const res = await fetch(`${API_BASE}${tradingPath("/trading/orders")}`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json", ...liveConfirmHeaders(confirm) },
    body: JSON.stringify(ticket),
  });
  checkUnauthorized(res);
  if (res.status === 422) {
    const body = (await res.json()) as { detail: TradingRejection };
    throw new OrderRejectedError(body.detail);
  }
  if (!res.ok) {
    throw new Error(await extractErrorMessage(res, `Order failed: ${res.status}`));
  }
  return (await res.json()) as { order: Order };
}

export async function deleteJson<T>(path: string, confirm?: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "DELETE",
    credentials: "include",
    headers: liveConfirmHeaders(confirm),
  });
  checkUnauthorized(res);
  if (res.status === 422) {
    const body = (await res.json()) as { detail: TradingRejection };
    throw new OrderRejectedError(body.detail);
  }
  if (!res.ok) {
    throw new Error(await extractErrorMessage(res, `DELETE ${path} failed: ${res.status}`));
  }
  return (await res.json()) as T;
}

export async function patchJson<T>(path: string, body: unknown, confirm?: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "PATCH",
    credentials: "include",
    headers: { "Content-Type": "application/json", ...liveConfirmHeaders(confirm) },
    body: JSON.stringify(body),
  });
  checkUnauthorized(res);
  if (res.status === 422) {
    const detail = (await res.json()) as { detail: TradingRejection };
    throw new OrderRejectedError(detail.detail);
  }
  if (!res.ok) {
    throw new Error(await extractErrorMessage(res, `PATCH ${path} failed: ${res.status}`));
  }
  return (await res.json()) as T;
}

export function cancelOrder(orderId: string, confirm?: string): Promise<{ cancelled: string }> {
  return deleteJson<{ cancelled: string }>(
    tradingPath(`/trading/orders/${encodeURIComponent(orderId)}`),
    confirm,
  );
}

/** Move a working stop order to a new price. The symbol rides along as a
 * cross-check -- the server refuses if the id and the symbol disagree. */
export function replaceStop(
  orderId: string,
  symbol: string,
  stopPrice: number,
  confirm?: string,
): Promise<{ order: Order }> {
  return patchJson<{ order: Order }>(
    tradingPath(`/trading/orders/${encodeURIComponent(orderId)}`),
    { symbol, stop_price: stopPrice },
    confirm,
  );
}

/** Move a working take-profit order to a new price. Same cross-check
 * convention as replaceStop, on the distinct /target path the take-profit
 * leg needs (see OrderService.replace_target). */
export function replaceTarget(
  orderId: string,
  symbol: string,
  limitPrice: number,
  confirm?: string,
): Promise<{ order: Order }> {
  return patchJson<{ order: Order }>(
    tradingPath(`/trading/orders/${encodeURIComponent(orderId)}/target`),
    { symbol, limit_price: limitPrice },
    confirm,
  );
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

export function closePosition(symbol: string, qty?: number, confirm?: string): Promise<CloseResult> {
  const suffix = qty != null ? `?qty=${encodeURIComponent(qty)}` : "";
  return deleteJson<CloseResult>(
    tradingPath(`/trading/positions/${encodeURIComponent(symbol)}${suffix}`),
    confirm,
  );
}
