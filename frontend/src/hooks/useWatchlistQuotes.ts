import { useEffect, useRef, useState } from "react";

import { getWatchlistQuotes } from "../api/http";
import type { WatchlistQuotes } from "../types/watchlist";

// Matches settings.scanner_poll_interval_regular's default -- no reason for
// the watchlist to refresh faster than the scanner itself does.
const POLL_MS = 5_000;

export interface WatchlistQuotesState {
  quotes: WatchlistQuotes;
  loading: boolean;
  error: string | null;
}

/**
 * Polls /api/watchlist/quotes for the current symbol list. A poll, not the
 * chart WebSocket's per-symbol subscriptions: StreamManager caps concurrent
 * live subscriptions at settings.max_stream_symbols (30), which a
 * ~100-symbol watchlist plus any open chart widgets would exceed -- see
 * routers/watchlist.py.
 */
export function useWatchlistQuotes(symbols: string[]): WatchlistQuotesState {
  const [quotes, setQuotes] = useState<WatchlistQuotes>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const symbolsRef = useRef(symbols);
  symbolsRef.current = symbols;
  // The panel passes a fresh array identity on every render; joining to a
  // string for the dependency comparison is what lets the effect below
  // restart only when the list actually changes, not on every render --
  // same reasoning as useScreenFeed serializing `screen` for its dependency.
  const key = symbols.join(",");

  useEffect(() => {
    let cancelled = false;

    const refresh = () => {
      if (symbolsRef.current.length === 0) {
        setQuotes({});
        setLoading(false);
        return;
      }
      getWatchlistQuotes(symbolsRef.current)
        .then((res) => {
          if (cancelled) return;
          setQuotes(res);
          setError(null);
        })
        .catch((err: unknown) => {
          if (!cancelled) setError(err instanceof Error ? err.message : String(err));
        })
        .finally(() => {
          if (!cancelled) setLoading(false);
        });
    };

    refresh();
    const interval = setInterval(refresh, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [key]);

  return { quotes, loading, error };
}
