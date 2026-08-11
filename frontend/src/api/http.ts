import type { ScannerRow, SymbolBarsResponse } from "../types/alpaca";
import type { MarketConditionsResponse } from "../types/marketConditions";
import type { ScannerBenchmarkResponse } from "../types/scannerBenchmark";
import type { ScannerHistoryResponse } from "../types/scannerHistory";
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
