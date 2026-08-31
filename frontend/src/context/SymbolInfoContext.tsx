import { createContext, useContext, useEffect, useState } from "react";
import type { ReactNode } from "react";

import { useSymbolInfo } from "../hooks/useSymbolInfo";
import type { SymbolInfoState } from "../hooks/useSymbolInfo";

export interface SymbolInfoContextValue {
  symbolInfo: SymbolInfoState;
  /** Publish times (unix seconds) of stories whose chart pin was clicked --
   * see SymbolInfoPanel's own highlightTimes prop. */
  highlightedNews: number[] | null;
  setHighlightedNews: (times: number[] | null) => void;
}

/** A single useSymbolInfo(symbol) fetch, shared by ChartWidget (which needs
 * it for the chart's own news-pin markers) and SymbolInfoWidget (which
 * renders it) -- same reasoning as TradingContext: without this, splitting
 * the company-info/news panel into its own widget would mean either a
 * second fetch of the same payload, or threading it through App.tsx's
 * memoized `widgets` object, which is deliberately kept free of anything
 * that changes mid-render (see TradingContext.tsx's own comment) since a
 * fresh dependency there would recompute that memo and remount CandleChart.
 */
const SymbolInfoContext = createContext<SymbolInfoContextValue | null>(null);

export function SymbolInfoProvider({ symbol, children }: { symbol: string | null; children: ReactNode }) {
  const symbolInfo = useSymbolInfo(symbol);
  const [highlightedNews, setHighlightedNews] = useState<number[] | null>(null);
  // Cleared on symbol change so a stale highlight cannot mark a different
  // stock's story -- same effect ChartWidget ran locally before this split.
  useEffect(() => setHighlightedNews(null), [symbol]);

  return (
    <SymbolInfoContext.Provider value={{ symbolInfo, highlightedNews, setHighlightedNews }}>
      {children}
    </SymbolInfoContext.Provider>
  );
}

export function useSymbolInfoContext(): SymbolInfoContextValue {
  const value = useContext(SymbolInfoContext);
  if (!value) throw new Error("useSymbolInfoContext must be used within a SymbolInfoProvider");
  return value;
}
