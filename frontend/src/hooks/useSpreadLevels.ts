import { useMemo } from "react";


import { useSpreadLevelsContext } from "../context/SpreadLevelsContext";
import { useChartPalette } from "./useSettings";
import type { IndicatorResult } from "../types/alpaca";
import { FLIP_COLOR, negativeColor, positiveColor } from "./useGexLevels";

/** The spread's strikes and underlying triggers as a "level"-kind
 * indicator for CandleChart -- the same shape useGexLevels produces, so
 * ChartWidget merges it into the Levels dropdown without knowing what it
 * is. Null when the widget has nothing for this symbol. */
export function useSpreadLevels(symbol: string | null): IndicatorResult | null {
  const { levels } = useSpreadLevelsContext();
  const { palette } = useChartPalette();
  return useMemo(() => {
    if (!symbol || !levels || levels.symbol !== symbol) return null;
    const series: Record<string, number> = {};
    const colors: Record<string, string> = {};
    for (const strike of levels.strikes) {
      series[strike.label] = strike.price;
      colors[strike.label] = strike.role === "long" ? positiveColor() : negativeColor();
    }
    if (levels.closeBelow != null) {
      series["Close below"] = levels.closeBelow;
      colors["Close below"] = FLIP_COLOR;
    }
    if (levels.closeAbove != null) {
      series["Close above"] = levels.closeAbove;
      colors["Close above"] = FLIP_COLOR;
    }
    if (Object.keys(series).length === 0) return null;
    return {
      name: "Spread",
      kind: "level",
      series,
      colors,
      style: { width: 1, dash: "dashed" },
    } as IndicatorResult;
    // palette: the colours above are read at build time.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbol, levels, palette]);
}
