import { useCallback, useState } from "react";

import { DEFAULT_WATCHLIST_SYMBOLS } from "../data/defaultWatchlist";

const STORAGE_KEY = "watchlist:symbols";

function loadSymbols(): string[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return DEFAULT_WATCHLIST_SYMBOLS;
    const parsed = JSON.parse(raw);
    if (Array.isArray(parsed) && parsed.every((s) => typeof s === "string")) {
      return parsed;
    }
  } catch {
    // Corrupt/foreign/stale localStorage value -- same fallback convention
    // as useDashboardLayout.loadLayout and useAlarms.loadEnabled.
  }
  return DEFAULT_WATCHLIST_SYMBOLS;
}

function save(symbols: string[]) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(symbols));
  } catch {
    // Storage disabled -- the list still works for this session.
  }
}

export interface WatchlistState {
  symbols: string[];
  addSymbol: (symbol: string) => void;
  removeSymbol: (symbol: string) => void;
}

/**
 * The watchlist's symbol list, persisted to localStorage -- same hand-rolled
 * convention as useAlarms/useDashboardLayout, not a backend table, since
 * this is a per-browser preference like everything else in the dashboard.
 * A fresh browser starts from DEFAULT_WATCHLIST_SYMBOLS (symbols_pinned.txt);
 * any add/remove after that persists and DEFAULT_WATCHLIST_SYMBOLS is never
 * consulted again.
 */
export function useWatchlist(): WatchlistState {
  const [symbols, setSymbols] = useState<string[]>(loadSymbols);

  const addSymbol = useCallback((symbol: string) => {
    const upper = symbol.trim().toUpperCase();
    if (!upper) return;
    setSymbols((prev) => {
      if (prev.includes(upper)) return prev;
      const next = [...prev, upper];
      save(next);
      return next;
    });
  }, []);

  const removeSymbol = useCallback((symbol: string) => {
    setSymbols((prev) => {
      const next = prev.filter((s) => s !== symbol);
      save(next);
      return next;
    });
  }, []);

  return { symbols, addSymbol, removeSymbol };
}
