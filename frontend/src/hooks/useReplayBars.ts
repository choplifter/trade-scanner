import { useEffect, useState } from "react";

import { getReplayBars } from "../api/replay";
import type { Bar } from "../types/alpaca";

export interface ReplayBarsState {
  bars: Bar[];
  /** True only while the very first fetch for the current symbol is in
   * flight -- a background refetch on a later as_of tick doesn't flip this
   * back on, so the chart updates in place instead of flashing a loading
   * placeholder every tick. */
  loading: boolean;
  error: string | null;
}

/**
 * This replayed symbol's candles up through the replay clock's current
 * as_of -- refetched whenever `asOf` changes (see ChartWidget's
 * isReplaySymbol/usingReplayBars, which passes the same live-updating
 * session.as_of the seek slider/clock display use), so the chart advances
 * candle by candle in step with the ranked scanner rows instead of
 * sitting frozen on whatever it showed when the symbol was first selected.
 */
export function useReplayBars(symbol: string | null, asOf: string | null): ReplayBarsState {
  const [bars, setBars] = useState<Bar[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // A stale chart from the previous symbol must not linger while the new
  // one's first fetch is in flight.
  useEffect(() => {
    setBars([]);
    setError(null);
  }, [symbol]);

  useEffect(() => {
    if (!symbol || !asOf) return;
    let cancelled = false;
    setLoading((current) => current || bars.length === 0);
    getReplayBars(symbol)
      .then((res) => {
        if (cancelled) return;
        setBars(res.bars);
        setError(null);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "Failed to load replay bars");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // Deliberately not depending on `bars` here: it's read only to seed the
    // initial loading flag for a fetch already triggered by symbol/asOf --
    // adding it would refetch on every bar update this same effect just
    // produced.
  }, [symbol, asOf]);

  return { bars, loading, error };
}
