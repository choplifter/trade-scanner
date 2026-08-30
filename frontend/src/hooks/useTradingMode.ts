import { useEffect, useState } from "react";

import { getTradingMode, setTradingMode, subscribeTradingMode, type TradingMode } from "../api/tradingMode";

export interface TradingModeState {
  mode: TradingMode;
  setMode: (mode: TradingMode) => void;
}

/** Thin React view over api/tradingMode.ts's singleton -- the state itself
 * lives there (so the http layer can read it without a hook), this just
 * re-renders whenever it changes. */
export function useTradingMode(): TradingModeState {
  const [mode, setModeState] = useState<TradingMode>(getTradingMode);

  useEffect(() => subscribeTradingMode(setModeState), []);

  return { mode, setMode: setTradingMode };
}
