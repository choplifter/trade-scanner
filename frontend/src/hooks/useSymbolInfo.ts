import { useEffect, useState } from "react";

import { getSymbolInfo } from "../api/http";
import type { SymbolInfoResponse } from "../types/symbolInfo";

export interface SymbolInfoState {
  info: SymbolInfoResponse | null;
  error: string | null;
  loading: boolean;
}

const EMPTY_STATE: SymbolInfoState = { info: null, error: null, loading: false };

/** One-shot fetch, same pattern as useHistoricalBars -- company info/news
 * doesn't need live updates the way the chart does. */
export function useSymbolInfo(symbol: string | null): SymbolInfoState {
  const [state, setState] = useState<SymbolInfoState>(EMPTY_STATE);

  useEffect(() => {
    if (!symbol) {
      setState(EMPTY_STATE);
      return;
    }

    let cancelled = false;
    setState({ ...EMPTY_STATE, loading: true });

    getSymbolInfo(symbol)
      .then((info) => {
        if (!cancelled) setState({ info, error: null, loading: false });
      })
      .catch((err: unknown) => {
        if (!cancelled) setState((s) => ({ ...s, error: String(err), loading: false }));
      });

    return () => {
      cancelled = true;
    };
  }, [symbol]);

  return state;
}
