import { useEffect, useMemo, useState } from "react";

import { deltaLevels } from "../api/options";
import type { IndicatorResult } from "../types/alpaca";
import type { DeltaLevelsResponse } from "../types/options";

// The chain moves slowly at 30-60 days; the GEX reading polls at the same pace.
const REFRESH_MS = 5 * 60_000;
// One colour for both lines, like the EM band: together they mark a range,
// not a support and a resistance -- and apart from the wall colours, so a
// 16-delta strike that sits on a wall still reads as two things.
export const DELTA_LINE_COLOR = "#c9922e";
export const DELTA_LINES_NAME = "Delta lines";

/**
 * The put and call strike nearest `delta` on the 30-60 day expiry, as a
 * level set for the chart -- backend app/options/delta_levels.py. Fetched
 * on symbol or delta change and every five minutes after; null for no
 * symbol (a contract's premium chart passes none) or no such expiry.
 */
export function useDeltaLevels(symbol: string | null, delta: number): IndicatorResult | null {
  const [reading, setReading] = useState<DeltaLevelsResponse | null>(null);

  useEffect(() => {
    setReading(null);
    if (!symbol) return;
    let cancelled = false;
    const load = () => {
      deltaLevels(symbol, delta)
        .then((next) => {
          if (!cancelled) setReading(next);
        })
        .catch(() => {
          // No 30-60 day expiry, or no chain: the lines simply are not drawn.
          if (!cancelled) setReading(null);
        });
    };
    load();
    const timer = window.setInterval(load, REFRESH_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [symbol, delta]);

  // Memoized on the reading: a new object every render would have the
  // chart tear down and redraw its price lines on every trade tick.
  return useMemo(() => deltaLevelsFrom(reading), [reading]);
}

/** The level set for a reading in hand. Labels carry the delta and the
 * expiry ("16Δ 45d put") so a line cannot be read as the near expiry's. */
export function deltaLevelsFrom(reading: DeltaLevelsResponse | null): IndicatorResult | null {
  if (!reading) return null;
  const tag = `${Math.round(reading.delta * 100)}Δ ${reading.dte}d`;
  const series: Record<string, number> = {};
  const colors: Record<string, string> = {};
  if (reading.put) {
    series[`${tag} put`] = reading.put.strike;
    colors[`${tag} put`] = DELTA_LINE_COLOR;
  }
  if (reading.call) {
    series[`${tag} call`] = reading.call.strike;
    colors[`${tag} call`] = DELTA_LINE_COLOR;
  }
  if (Object.keys(series).length === 0) return null;
  return { name: DELTA_LINES_NAME, kind: "level", series, colors, style: { dash: "large-dashed" } };
}
