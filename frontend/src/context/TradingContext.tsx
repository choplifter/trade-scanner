import { createContext, useContext } from "react";
import type { ReactNode } from "react";

import { useTrading } from "../hooks/useTrading";
import type { TradingActions, TradingState } from "../hooks/useTrading";

/** A single `useTrading()` poll loop, shared by every consumer. Introduced
 * because ChartWidget needs read access to positions/orders (to draw them on
 * the chart) without either starting a second, out-of-sync poll loop or
 * lifting the state into App's `widgets` useMemo -- that memo is deliberately
 * kept free of anything that changes on a poll tick, since a fresh dependency
 * there would recompute it on every tick and remount CandleChart. */
const TradingContext = createContext<(TradingState & TradingActions) | null>(null);

export function TradingProvider({ children }: { children: ReactNode }) {
  const trading = useTrading();
  return <TradingContext.Provider value={trading}>{children}</TradingContext.Provider>;
}

export function useTradingContext(): TradingState & TradingActions {
  const value = useContext(TradingContext);
  if (!value) throw new Error("useTradingContext must be used within a TradingProvider");
  return value;
}
