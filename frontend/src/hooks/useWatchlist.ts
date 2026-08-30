import { useCallback, useEffect, useState } from "react";

import { addWatchlistSymbol, getWatchlistSymbols, removeWatchlistSymbol } from "../api/http";

export interface WatchlistState {
  symbols: string[];
  addSymbol: (symbol: string) => void;
  removeSymbol: (symbol: string) => void;
}

/**
 * The watchlist's symbol list, now per-user backend state (see
 * app.watchlist.store.WatchlistStore) rather than localStorage -- it was
 * per-browser before real logins existed, which meant "your" watchlist
 * didn't actually follow you. A fresh account is seeded server-side from
 * the same symbols_pinned.txt-derived default list this hook used to fall
 * back to locally (see app.watchlist.defaults).
 *
 * Optimistic add/remove: the local list updates immediately, and is
 * reconciled with the server's response (which is the authoritative list,
 * e.g. after de-duplication) once the request resolves.
 */
export function useWatchlist(): WatchlistState {
  const [symbols, setSymbols] = useState<string[]>([]);

  useEffect(() => {
    let cancelled = false;
    getWatchlistSymbols()
      .then((res) => {
        if (!cancelled) setSymbols(res.symbols);
      })
      .catch(() => {
        // Left empty rather than falling back to a local default list --
        // an empty watchlist on a fetch failure is at least honest about
        // not knowing the real list, rather than showing one that isn't.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const addSymbol = useCallback((symbol: string) => {
    const upper = symbol.trim().toUpperCase();
    if (!upper) return;
    setSymbols((prev) => (prev.includes(upper) ? prev : [...prev, upper]));
    addWatchlistSymbol(upper)
      .then((res) => setSymbols(res.symbols))
      .catch(() => {
        // The optimistic add stands even if the server rejected it silently
        // failing here would just leave the list one entry ahead of the
        // server's until the next full reload -- not worth a retry dance
        // for a watchlist.
      });
  }, []);

  const removeSymbol = useCallback((symbol: string) => {
    setSymbols((prev) => prev.filter((s) => s !== symbol));
    removeWatchlistSymbol(symbol)
      .then((res) => setSymbols(res.symbols))
      .catch(() => {
        // Same tradeoff as addSymbol above.
      });
  }, []);

  return { symbols, addSymbol, removeSymbol };
}
