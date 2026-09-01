import { useEffect, useState } from "react";

import { getGex } from "../api/http";
import type { IndicatorResult } from "../types/alpaca";
import type { GexSymbolReading } from "../types/gex";

const REFRESH_MS = 5 * 60_000;
// Matches the light-theme hex values CandleChart's own position lines use
// (POSITION_TARGET_COLOR/POSITION_STOP_COLOR) -- this chart's price-line
// colors aren't theme-reactive anywhere today, so this doesn't introduce a
// new pattern.
const POSITIVE_COLOR = "#0ca30c";
const NEGATIVE_COLOR = "#d03b3b";

/**
 * The top-3 gamma-wall strikes for `symbol` (SPY/QQQ only -- see backend
 * app.market_data.gamma_exposure.SYMBOLS), as a "level"-kind IndicatorResult
 * ready to splice into CandleChart's `indicators` prop alongside its normal
 * bar-computed indicators. Pass `null` for any other symbol.
 *
 * One-shot-fetch-on-symbol-change like useSymbolInfo, plus a poll interval:
 * GEX only refreshes server-side every 30 minutes (gex_refresh_interval),
 * but the chart can stay mounted on SPY/QQQ far longer than that.
 */
export function useGexLevels(symbol: string | null): IndicatorResult | null {
  const [reading, setReading] = useState<GexSymbolReading | null>(null);

  useEffect(() => {
    if (!symbol) {
      setReading(null);
      return;
    }

    let cancelled = false;
    const fetchGex = () => {
      getGex()
        .then((res) => {
          if (!cancelled) setReading(res.symbols[symbol] ?? null);
        })
        .catch(() => {});
    };
    fetchGex();
    const interval = setInterval(fetchGex, REFRESH_MS);

    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [symbol]);

  if (!reading || reading.top_walls.length === 0) return null;

  const series: Record<string, number> = {};
  const colors: Record<string, string> = {};
  for (const wall of reading.top_walls) {
    const label = `GEX ${wall.strike}`;
    series[label] = wall.strike;
    colors[label] = wall.net_gex >= 0 ? POSITIVE_COLOR : NEGATIVE_COLOR;
  }
  return { name: "GEX Wall", kind: "level", series, colors };
}
