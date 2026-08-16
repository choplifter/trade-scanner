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

async function postJson<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { method: "POST" });
  if (!res.ok) {
    throw new Error(await extractErrorMessage(res, `POST ${path} failed: ${res.status}`));
  }
  return (await res.json()) as T;
}

export interface ScannerResponse {
  scanner: string;
  session: string;
  is_latest_session: boolean;
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
