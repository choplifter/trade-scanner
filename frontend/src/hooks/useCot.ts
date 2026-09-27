import { useEffect, useState } from "react";

import { getSymbolCot } from "../api/http";
import type { CotReading } from "../types/options";

/**
 * Positioning in the futures behind a commodity ETF (USO, UNG, GLD, SLV),
 * from the CFTC's weekly report -- see backend app/market_data/cot.py.
 *
 * Fetched once per symbol and never polled: the report changes on Friday
 * afternoons, and the backend caches it for six hours anyway. A symbol the
 * report does not cover answers null, which is the ordinary case and shows
 * as no line rather than as an error.
 */
export function useCot(symbol: string | null): CotReading | null {
  const [cot, setCot] = useState<CotReading | null>(null);

  useEffect(() => {
    if (!symbol) {
      setCot(null);
      return;
    }
    let cancelled = false;
    setCot(null);
    getSymbolCot(symbol)
      .then((res) => {
        if (!cancelled && res.symbol === symbol.toUpperCase()) setCot(res.cot);
      })
      .catch(() => {
        // Context, not a number anything depends on: no line is the right
        // way to say "could not ask".
      });
    return () => {
      cancelled = true;
    };
  }, [symbol]);

  return cot;
}
