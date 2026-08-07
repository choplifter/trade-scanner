import { useEffect, useState } from "react";

import { getSymbolBars } from "../api/http";
import { chartSocket } from "../api/ws";
import type { Bar } from "../types/alpaca";

export interface ChartFeedState {
  bars: Bar[];
  vwap: (number | null)[];
  error: string | null;
  loading: boolean;
}

const EMPTY_STATE: ChartFeedState = {
  bars: [],
  vwap: [],
  error: null,
  loading: false,
};

/**
 * Seeds from today's REST bar history, then appends each live 1-minute bar
 * as it arrives over the socket -- so `bars`/`vwap` always hold the full,
 * ordered 1-minute sequence for the session, which is what timeframe
 * aggregation (see aggregateBars) needs as its input.
 */
export function useChartFeed(symbol: string | null): ChartFeedState {
  const [state, setState] = useState<ChartFeedState>(EMPTY_STATE);

  useEffect(() => {
    if (!symbol) {
      setState(EMPTY_STATE);
      return;
    }

    let cancelled = false;
    setState({ ...EMPTY_STATE, loading: true });

    getSymbolBars(symbol)
      .then((res) => {
        if (!cancelled) {
          setState((s) => ({ ...s, bars: res.bars, vwap: res.vwap, loading: false }));
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setState((s) => ({ ...s, error: String(err), loading: false }));
        }
      });

    const unsubscribe = chartSocket.subscribe(symbol, (msg) => {
      if (msg.type === "bar") {
        setState((s) => {
          const bars = [...s.bars];
          const vwap = [...s.vwap];
          const last = bars[bars.length - 1];
          if (last && last.t === msg.bar.t) {
            bars[bars.length - 1] = msg.bar;
            vwap[vwap.length - 1] = msg.vwap;
          } else {
            bars.push(msg.bar);
            vwap.push(msg.vwap);
          }
          return { ...s, bars, vwap, error: null };
        });
      } else {
        setState((s) => ({ ...s, error: msg.message }));
      }
    });

    return () => {
      cancelled = true;
      unsubscribe();
    };
  }, [symbol]);

  return state;
}
