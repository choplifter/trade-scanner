import { useEffect, useState } from "react";

import { getReplayIndicators } from "../api/replay";
import type { IndicatorResult } from "../types/alpaca";

export interface ReplayIndicatorsState {
  vwap: (number | null)[];
  vwapPremarket: (number | null)[];
  indicators: IndicatorResult[];
  loading: boolean;
  error: string | null;
}

const EMPTY: Omit<ReplayIndicatorsState, "loading" | "error"> = {
  vwap: [],
  vwapPremarket: [],
  indicators: [],
};

/**
 * This replayed symbol's VWAP/EMA/range overlays as of the replay clock's
 * current as_of -- refetched whenever `asOf` changes, same trigger
 * useReplayBars uses, so the two stay in step. Split from useReplayBars
 * because this one costs real Alpaca calls (weekly/monthly/hourly bars),
 * unlike the bars fetch which is a cheap in-memory slice -- see
 * getReplayIndicators.
 */
export function useReplayIndicators(symbol: string | null, asOf: string | null): ReplayIndicatorsState {
  const [state, setState] = useState<Omit<ReplayIndicatorsState, "loading" | "error">>(EMPTY);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // A stale set of indicators from the previous symbol must not linger
  // while the new one's first fetch is in flight.
  useEffect(() => {
    setState(EMPTY);
    setError(null);
  }, [symbol]);

  useEffect(() => {
    if (!symbol || !asOf) return;
    let cancelled = false;
    setLoading((current) => current || state.indicators.length === 0);
    getReplayIndicators(symbol)
      .then((res) => {
        if (cancelled) return;
        setState({ vwap: res.vwap, vwapPremarket: res.vwap_premarket, indicators: res.indicators });
        setError(null);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "Failed to load replay indicators");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // Deliberately not depending on `state` here: it's read only to seed the
    // initial loading flag for a fetch already triggered by symbol/asOf --
    // adding it would refetch on every update this same effect just produced.
  }, [symbol, asOf]);

  return { ...state, loading, error };
}
