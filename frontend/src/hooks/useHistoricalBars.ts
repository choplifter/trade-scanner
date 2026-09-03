import { useEffect, useState } from "react";

import { getSymbolBars } from "../api/http";
import { parseOcc } from "../utils/occ";
import type { Bar, IndicatorResult } from "../types/alpaca";
import { useStrategySettingsNonce } from "./useStrategySettingsNonce";

export interface HistoricalBarsState {
  bars: Bar[];
  vwap: (number | null)[];
  /** This timeframe's own indicators. Which ones come back is decided
   * server-side -- a level describing a period the chart draws as a single
   * candle is left out entirely (see MAX_TIMEFRAME in the backend's
   * indicator loader), so this is NOT interchangeable with the intraday
   * feed's list. */
  indicators: IndicatorResult[];
  error: string | null;
  loading: boolean;
}

/** Refresh cadence of a premium chart on a higher timeframe. */
const OPTION_POLL_MS = 30000;

const EMPTY_STATE: HistoricalBarsState = {
  bars: [],
  vwap: [],
  indicators: [],
  error: null,
  loading: false,
};

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
  // Same contract as useChartFeed: signal settings shape the indicator set.
  const settingsNonce = useStrategySettingsNonce();

  useEffect(() => {
    if (!symbol || !alpacaTimeframe) {
      setState(EMPTY_STATE);
      return;
    }

    let cancelled = false;
    setState({ ...EMPTY_STATE, loading: true });

    getSymbolBars(symbol, alpacaTimeframe)
      .then((res) => {
        if (!cancelled)
          setState({
            bars: res.bars,
            vwap: res.vwap,
            indicators: res.indicators,
            error: null,
            loading: false,
          });
      })
      .catch((err: unknown) => {
        if (!cancelled) setState((s) => ({ ...s, error: String(err), loading: false }));
      });

    // A premium chart on a higher timeframe has no live tick either; a slow
    // full re-fetch keeps its newest candle current.
    const poll = parseOcc(symbol)
      ? window.setInterval(() => {
          if (cancelled || document.hidden) return;
          getSymbolBars(symbol, alpacaTimeframe)
            .then((res) => {
              if (!cancelled) setState({ bars: res.bars, vwap: res.vwap, indicators: res.indicators, error: null, loading: false });
            })
            .catch(() => {});
        }, OPTION_POLL_MS)
      : null;

    return () => {
      cancelled = true;
      if (poll != null) window.clearInterval(poll);
    };
  }, [symbol, alpacaTimeframe, settingsNonce]);

  return state;
}
