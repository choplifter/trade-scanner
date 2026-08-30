/** One /symbols/search result -- see list_active_equity_symbols. name is
 * null only via that endpoint's narrower fallback path (no credentials, or
 * the startup asset fetch failed), where only the scanner universe's own
 * symbol/exchange survive. */
export interface SymbolSuggestion {
  symbol: string;
  name: string | null;
  exchange: string;
}

export interface WatchlistQuote {
  last: number;
  prevClose: number | null;
  pctChange: number | null;
  volume: number | null;
  updatedAt: string | null;
}

/** Keyed by symbol; a symbol absent from the map hasn't loaded a quote yet
 * (or the backend had nothing usable for it -- see routers/watchlist.py). */
export type WatchlistQuotes = Record<string, WatchlistQuote>;
