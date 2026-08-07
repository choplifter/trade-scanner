import { useEffect, useState } from "react";

import { getSymbolBars } from "../api/http";
import type { Bar } from "../types/alpaca";

export interface HistoricalBarsState {
  bars: Bar[];
  vwap: (number | null)[];
  error: string | null;
  loading: boolean;
}

const EMPTY_STATE: HistoricalBarsState = { bars: [], vwap: [], error: null, loading: false };

/**
 * One-shot fetch at a native Alpaca resolution (1Hour/4Hour/1Day/1Week/
 * 1Month) -- unlike useChartFeed's 1-minute intraday feed, this doesn't
 * subscribe to live updates; higher timeframes don't need tick-by-tick
 * freshness.
 */
export function useHistoricalBars(
  symbol: string | null,
  alpacaTimeframe: string | null,
): HistoricalBarsState {
  const [state, setState] = useState<HistoricalBarsState>(EMPTY_STATE);

  useEffect(() => {
    if (!symbol || !alpacaTimeframe) {
      setState(EMPTY_STATE);
      return;
    }

    let cancelled = false;
    setState({ ...EMPTY_STATE, loading: true });

    getSymbolBars(symbol, alpacaTimeframe)
      .then((res) => {
        if (!cancelled) setState({ bars: res.bars, vwap: res.vwap, error: null, loading: false });
      })
      .catch((err: unknown) => {
        if (!cancelled) setState((s) => ({ ...s, error: String(err), loading: false }));
      });

    return () => {
      cancelled = true;
    };
  }, [symbol, alpacaTimeframe]);

  return state;
}
